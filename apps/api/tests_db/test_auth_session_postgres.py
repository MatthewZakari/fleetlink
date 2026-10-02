"""Real session constraints, caller transactions and competing optimistic writers."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import DateTime, delete, inspect, text
from sqlalchemy.exc import InvalidRequestError

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
    SessionNotFound,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.domain.user import User
from fleetlink.modules.identity.infrastructure.models import AuthenticationSessionRecord, UserRecord
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
USER = User(UUID(int=1009), CREATED)
SESSION = AuthenticationSession(
    UUID(int=1010), USER.id, UUID(int=1011), CREATED, CREATED + timedelta(days=1)
)
MISSING = UUID(int=1012)


@asynccontextmanager
async def database_for_test(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 3}))
    try:
        yield database
    finally:
        try:
            async with database.session() as session, session.begin():
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.user_id == USER.id
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id == USER.id))
        finally:
            await database.dispose()


def test_schema(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database, database.connection() as connection:
            columns = await connection.run_sync(
                lambda conn: inspect(conn).get_columns("identity_auth_sessions")
            )
            assert {c["name"] for c in columns} == {
                "id",
                "user_id",
                "family_id",
                "created_at",
                "expires_at",
                "status",
                "version",
            }
            assert all(not c["nullable"] and c["default"] is None for c in columns)
            for column in columns:
                if column["name"] in {"id", "user_id", "family_id"}:
                    assert str(column["type"]) == "UUID"
                if column["name"] in {"created_at", "expires_at"}:
                    assert isinstance(column["type"], DateTime) and column["type"].timezone
            pk = await connection.run_sync(
                lambda conn: inspect(conn).get_pk_constraint("identity_auth_sessions")
            )
            assert pk["constrained_columns"] == ["id"]
            checks = await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("identity_auth_sessions")
            )
            assert {c["name"] for c in checks} == {
                "ck_identity_auth_sessions_status",
                "ck_identity_auth_sessions_version",
                "ck_identity_auth_sessions_expiry",
            }
            indexes = await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("identity_auth_sessions")
            )
            assert len(indexes) == 1
            assert indexes[0]["column_names"] == ["family_id"] and indexes[0]["unique"]
            fks = await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("identity_auth_sessions")
            )
            assert len(fks) == 1 and fks[0]["referred_table"] == "identity_users"
            assert fks[0]["options"]["ondelete"] == "RESTRICT"

    asyncio.run(run())


def test_round_trip_and_terminal_revocation(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        offset = timezone(timedelta(hours=5))
        original = replace(
            SESSION,
            created_at=CREATED.astimezone(offset),
            expires_at=SESSION.expires_at.astimezone(offset),
        )
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(USER)
                repository: AuthenticationSessionRepository = (
                    SqlAlchemyAuthenticationSessionRepository(session)
                )
                await repository.add(original)
                assert await repository.get(SESSION.id) == original
            async with database.session() as session, session.begin():
                await session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
                repository = SqlAlchemyAuthenticationSessionRepository(session)
                loaded = await repository.get_by_family(SESSION.family_id)
                assert loaded == original and loaded is not original
                assert loaded.created_at.tzinfo is UTC and loaded.expires_at.tzinfo is UTC
                saved = await repository.save(loaded.revoke())
                assert saved.version == 1 and saved.status is SessionStatus.REVOKED
                assert await repository.get(SESSION.id) == saved
            for attempted in (original, replace(saved, status=SessionStatus.ACTIVE)):
                with pytest.raises(SessionConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyAuthenticationSessionRepository(session).save(attempted)
            async with database.session() as session, session.begin():
                repository = SqlAlchemyAuthenticationSessionRepository(session)
                assert await repository.get(SESSION.id) == saved
                assert (await repository.save(saved.revoke())).version == 2

    asyncio.run(run())


def test_missing_metadata_versions_and_explicit_begin(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                repository = SqlAlchemyAuthenticationSessionRepository(session)
                assert await repository.get(MISSING) is None
                assert await repository.get_by_family(MISSING) is None
                await SqlAlchemyUserRepository(session).add(USER)
                await repository.add(SESSION)
            with pytest.raises(SessionNotFound):
                async with database.session() as session, session.begin():
                    await SqlAlchemyAuthenticationSessionRepository(session).save(
                        replace(SESSION, id=MISSING)
                    )
            for changed in (
                replace(SESSION, user_id=MISSING),
                replace(SESSION, family_id=MISSING),
                replace(SESSION, created_at=CREATED - timedelta(seconds=1)),
                replace(SESSION, expires_at=SESSION.expires_at + timedelta(seconds=1)),
                replace(SESSION, version=1),
            ):
                with pytest.raises(SessionConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyAuthenticationSessionRepository(session).save(changed)
            with pytest.raises(SessionConflict):
                async with database.session() as session, session.begin():
                    await SqlAlchemyAuthenticationSessionRepository(session).add(
                        replace(SESSION, id=MISSING, version=1)
                    )
            with pytest.raises(InvalidRequestError, match="Autobegin"):
                async with database.session() as session:
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)

    asyncio.run(run())


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO identity_auth_sessions SELECT * FROM identity_auth_sessions WHERE id = :id",
        "UPDATE identity_auth_sessions SET user_id = :missing WHERE id = :id",
        "DELETE FROM identity_users WHERE id = :user",
        "UPDATE identity_auth_sessions SET status = 'expired' WHERE id = :id",
        "UPDATE identity_auth_sessions SET status = NULL WHERE id = :id",
        "UPDATE identity_auth_sessions SET version = -1 WHERE id = :id",
        "UPDATE identity_auth_sessions SET expires_at = created_at WHERE id = :id",
        "UPDATE identity_auth_sessions SET expires_at = created_at - interval '1 second' "
        "WHERE id = :id",
    ],
)
def test_constraints_rollback_and_sanitization(
    settings: Settings, migrated_database: None, statement: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(DatabaseError) as caught:
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).add(USER)
                    await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
                    await session.execute(
                        text(statement), {"id": SESSION.id, "user": USER.id, "missing": MISSING}
                    )
            assert str(SESSION.id) not in str(caught.value) and statement not in str(caught.value)
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) is None
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id) is None
                )

    asyncio.run(run())


def test_add_constraints(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(DatabaseError):
                async with database.session() as session, session.begin():
                    await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(USER)
                await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
            for duplicate in (SESSION, replace(SESSION, id=MISSING)):
                with pytest.raises(DatabaseError):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyAuthenticationSessionRepository(session).add(duplicate)

    asyncio.run(run())


def test_composed_failure_and_uncommitted_isolation(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as writer, writer.begin():
                    await SqlAlchemyUserRepository(writer).add(USER)
                    repository = SqlAlchemyAuthenticationSessionRepository(writer)
                    await repository.add(SESSION)
                    await repository.save(SESSION.revoke())
                    async with database.session() as reader, reader.begin():
                        assert (
                            await SqlAlchemyAuthenticationSessionRepository(reader).get(SESSION.id)
                            is None
                        )
                    raise RuntimeError("injected")
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) is None
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id) is None
                )

    asyncio.run(run())


def test_competing_saves_have_one_winner(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(USER)
                await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
            barrier = asyncio.Barrier(2)

            async def change() -> AuthenticationSession | None:
                try:
                    async with database.session() as session, session.begin():
                        repository = SqlAlchemyAuthenticationSessionRepository(session)
                        loaded = await repository.get(SESSION.id)
                        assert loaded == SESSION
                        await barrier.wait()
                        return await repository.save(loaded.revoke())
                except SessionConflict:
                    return None

            results = await asyncio.wait_for(asyncio.gather(change(), change()), timeout=15)
            winners = [result for result in results if result is not None]
            assert len(winners) == 1 and winners[0].version == 1
            async with database.session() as session, session.begin():
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == winners[0]
                )

    asyncio.run(run())
