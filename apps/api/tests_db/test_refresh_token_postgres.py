"""Real refresh constraints, atomic rotation, rollback and session/token races."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import InvalidRequestError

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshRotation,
    RefreshTokenConflict,
    RefreshTokenNotFound,
    RefreshTokenRepository,
)
from fleetlink.modules.identity.application.session_ports import SessionConflict
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshSessionUnavailable,
    RefreshTokenExpired,
    RefreshTokenRecord,
    RefreshTokenReuse,
    RefreshVerifier,
)
from fleetlink.modules.identity.domain.user import User
from fleetlink.modules.identity.infrastructure.models import (
    AuthenticationSessionRecord,
    UserRecord,
)
from fleetlink.modules.identity.infrastructure.models import (
    RefreshTokenRecord as Record,
)
from fleetlink.modules.identity.infrastructure.refresh_repository import (
    SqlAlchemyRefreshTokenRepository,
)
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
AT = CREATED + timedelta(minutes=1)
USER = User(UUID(int=1100), CREATED)
SESSION = AuthenticationSession(
    UUID(int=1101), USER.id, UUID(int=1102), CREATED, CREATED + timedelta(days=1)
)
TOKEN = RefreshTokenRecord(
    UUID(int=1103),
    SESSION.id,
    RefreshVerifier(b"test-digest-a"),
    CREATED,
    CREATED + timedelta(hours=1),
)
NEXT = RefreshTokenRecord(
    UUID(int=1104), SESSION.id, RefreshVerifier(b"test-digest-b"), AT, TOKEN.expires_at
)
MISSING = UUID(int=1199)


@asynccontextmanager
async def database_for_test(settings: Settings, *, seed: bool = True) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 3}))
    try:
        if seed:
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(USER)
                await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
                await SqlAlchemyRefreshTokenRepository(session).add(TOKEN)
        yield database
    finally:
        try:
            async with database.session() as session, session.begin():
                await session.execute(delete(Record).where(Record.session_id == SESSION.id))
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.user_id == USER.id
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id == USER.id))
        finally:
            await database.dispose()


def test_rotation_round_trip_and_reuse(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                await session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
                repository: RefreshTokenRepository = SqlAlchemyRefreshTokenRepository(session)
                loaded = await repository.get(TOKEN.id)
                assert loaded == TOKEN and loaded is not TOKEN and loaded.created_at.tzinfo is UTC
                assert await repository.get(MISSING) is None
                rotated = await repository.rotate(loaded, NEXT, SESSION, at=AT)
                assert rotated.session == replace(SESSION, version=1)
                assert rotated.consumed == replace(TOKEN.consume(NEXT.id, AT), version=1)
                assert rotated.replacement == NEXT
            async with database.session() as session, session.begin():
                repository = SqlAlchemyRefreshTokenRepository(session)
                assert await repository.get(TOKEN.id) == rotated.consumed
                assert await repository.get(NEXT.id) == NEXT
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == rotated.session
                )
                with pytest.raises(RefreshTokenReuse):
                    await repository.rotate(rotated.consumed, NEXT, rotated.session, at=AT)
            # Even fabricated fresh versions cannot reactivate or erase consumption.
            for attempted in (TOKEN, replace(TOKEN, version=1)):
                with pytest.raises(RefreshTokenConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).rotate(
                            attempted, replace(NEXT, id=MISSING), rotated.session, at=AT
                        )
            async with database.session() as session, session.begin():
                assert (
                    await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id)
                    == rotated.consumed
                )
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == rotated.session
                )

    asyncio.run(run())


def test_missing_and_immutable_metadata(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(RefreshTokenNotFound):
                async with database.session() as session, session.begin():
                    await SqlAlchemyRefreshTokenRepository(session).rotate(
                        replace(TOKEN, id=MISSING), NEXT, SESSION, at=AT
                    )
            for changed in (
                replace(TOKEN, verifier=RefreshVerifier(b"changed")),
                replace(TOKEN, session_id=MISSING),
                replace(TOKEN, created_at=CREATED + timedelta(seconds=1)),
                replace(TOKEN, expires_at=TOKEN.expires_at + timedelta(seconds=1)),
                replace(TOKEN, version=1),
            ):
                with pytest.raises(RefreshTokenConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).rotate(
                            changed, NEXT, SESSION, at=AT
                        )
            for changed in (
                replace(NEXT, session_id=MISSING),
                replace(NEXT, version=1),
                replace(NEXT, created_at=CREATED),
                replace(NEXT, expires_at=SESSION.expires_at + timedelta(seconds=1)),
            ):
                with pytest.raises(RefreshTokenConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).rotate(
                            TOKEN, changed, SESSION, at=AT
                        )
            with pytest.raises(InvalidRequestError, match="Autobegin"):
                async with database.session() as session:
                    await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id)
            async with database.session() as session, session.begin():
                assert await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id) == TOKEN
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == SESSION
                )

    asyncio.run(run())


def test_composition_and_rotation_rollback(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings, seed=False) as database:
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).add(USER)
                    await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
                    repository = SqlAlchemyRefreshTokenRepository(session)
                    await repository.add(TOKEN)
                    await repository.rotate(TOKEN, NEXT, SESSION, at=AT)
                    raise RuntimeError("injected")
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) is None
                assert await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id) is None
        async with database_for_test(settings) as database:
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as session, session.begin():
                    await SqlAlchemyRefreshTokenRepository(session).rotate(
                        TOKEN, NEXT, SESSION, at=AT
                    )
                    async with database.session() as reader, reader.begin():
                        assert await SqlAlchemyRefreshTokenRepository(reader).get(TOKEN.id) == TOKEN
                        assert await SqlAlchemyRefreshTokenRepository(reader).get(NEXT.id) is None
                    raise RuntimeError("injected")
            async with database.session() as session, session.begin():
                assert await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id) == TOKEN
                assert await SqlAlchemyRefreshTokenRepository(session).get(NEXT.id) is None
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == SESSION
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE identity_refresh_tokens SET session_id = :missing WHERE id = :id",
        "DELETE FROM identity_auth_sessions WHERE id = :session",
        "UPDATE identity_refresh_tokens SET status = 'invalid' WHERE id = :id",
        "UPDATE identity_refresh_tokens SET status = NULL WHERE id = :id",
        "UPDATE identity_refresh_tokens SET version = -1 WHERE id = :id",
        "UPDATE identity_refresh_tokens SET expires_at = created_at WHERE id = :id",
        "UPDATE identity_refresh_tokens SET verifier = ''::bytea WHERE id = :id",
        "UPDATE identity_refresh_tokens SET verifier = repeat('x', 513)::bytea WHERE id = :id",
        "UPDATE identity_refresh_tokens SET consumed_at = created_at WHERE id = :id",
        "UPDATE identity_refresh_tokens SET status = 'consumed' WHERE id = :id",
        "UPDATE identity_refresh_tokens SET status = 'consumed', "
        "consumed_at = created_at, replaced_by_id = id WHERE id = :id",
        "UPDATE identity_refresh_tokens SET status = 'consumed', "
        "consumed_at = expires_at, replaced_by_id = :missing WHERE id = :id",
        "UPDATE identity_refresh_tokens SET status = 'consumed', "
        "consumed_at = created_at, replaced_by_id = :missing WHERE id = :id",
    ],
)
def test_constraints_and_safe_errors(
    settings: Settings, migrated_database: None, statement: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(DatabaseError) as caught:
                async with database.session() as session, session.begin():
                    await session.execute(
                        text(statement), {"id": TOKEN.id, "session": SESSION.id, "missing": MISSING}
                    )
            assert "test-digest" not in repr(caught.value) and statement not in str(caught.value)
            assert str(TOKEN.id) not in str(caught.value)

    asyncio.run(run())


def test_add_constraints_and_failed_insert_rolls_back_rotation(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            for invalid in (TOKEN, NEXT, replace(NEXT, session_id=MISSING)):
                with pytest.raises(DatabaseError):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).add(invalid)
            for invalid in (replace(NEXT, version=1), TOKEN.consume(NEXT.id, AT)):
                with pytest.raises(RefreshTokenConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).add(invalid)
            # A second rotation with a replacement ID already used by consumed A fails
            # after both CAS writes; the caller must roll back both advances.
            async with database.session() as session, session.begin():
                rotated = await SqlAlchemyRefreshTokenRepository(session).rotate(
                    TOKEN, NEXT, SESSION, at=AT
                )
            with pytest.raises(DatabaseError):
                async with database.session() as session, session.begin():
                    await SqlAlchemyRefreshTokenRepository(session).rotate(
                        NEXT, replace(NEXT, id=TOKEN.id), rotated.session, at=AT
                    )
            async with database.session() as session, session.begin():
                assert await SqlAlchemyRefreshTokenRepository(session).get(NEXT.id) == NEXT
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == rotated.session
                )

    asyncio.run(run())


@pytest.mark.parametrize("race", ["rotation", "token_cas", "revocation"])
def test_competing_writers_exactly_one_winner(
    settings: Settings, migrated_database: None, race: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            barrier = asyncio.Barrier(2)

            async def change(second: bool) -> RefreshRotation | AuthenticationSession | None:
                try:
                    async with database.session() as session, session.begin():
                        repository = SqlAlchemyRefreshTokenRepository(session)
                        sessions = SqlAlchemyAuthenticationSessionRepository(session)
                        token = await repository.get(TOKEN.id)
                        lineage = await sessions.get(SESSION.id)
                        assert token == TOKEN and lineage == SESSION
                        await barrier.wait()
                        if second and race == "revocation":
                            return await sessions.save(lineage.revoke())
                        if second and race == "token_cas":
                            # Deliberately anticipate the next session version to prove
                            # token CAS independently rejects stale consumption.
                            lineage = replace(lineage, version=1)
                        return await repository.rotate(
                            token, replace(NEXT, id=MISSING) if second else NEXT, lineage, at=AT
                        )
                except (SessionConflict, RefreshTokenConflict):
                    return None

            results = await asyncio.wait_for(
                asyncio.gather(change(False), change(True)), timeout=15
            )
            winners = [result for result in results if result is not None]
            assert len(winners) == 1
            async with database.session() as session, session.begin():
                rows = (await session.scalars(select(Record))).all()
                winner = winners[0]
                if isinstance(winner, RefreshRotation):
                    assert len(rows) == 2
                    assert (
                        await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id)
                        == winner.consumed
                    )
                    assert (
                        await SqlAlchemyRefreshTokenRepository(session).get(winner.replacement.id)
                        == winner.replacement
                    )
                    expected_session = winner.session
                else:
                    assert len(rows) == 1
                    assert await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id) == TOKEN
                    expected_session = winner
                assert expected_session.version == 1
                assert (
                    await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                    == expected_session
                )

    asyncio.run(run())


def test_revoked_expired_and_stale_sessions(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            for lineage in (SESSION.revoke(), replace(SESSION, expires_at=AT)):
                with pytest.raises(RefreshSessionUnavailable):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).rotate(
                            TOKEN, NEXT, lineage, at=AT
                        )
            with pytest.raises(RefreshTokenExpired):
                async with database.session() as session, session.begin():
                    await SqlAlchemyRefreshTokenRepository(session).rotate(
                        TOKEN, NEXT, SESSION, at=TOKEN.expires_at
                    )
            async with database.session() as session, session.begin():
                await SqlAlchemyAuthenticationSessionRepository(session).save(SESSION.revoke())
            for lineage in (SESSION, replace(SESSION, version=1)):
                with pytest.raises(SessionConflict):
                    async with database.session() as session, session.begin():
                        await SqlAlchemyRefreshTokenRepository(session).rotate(
                            TOKEN, NEXT, lineage, at=AT
                        )
            async with database.session() as session, session.begin():
                assert await SqlAlchemyRefreshTokenRepository(session).get(TOKEN.id) == TOKEN
                assert await SqlAlchemyRefreshTokenRepository(session).get(NEXT.id) is None

    asyncio.run(run())


def test_schema(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database, database.connection() as connection:
            columns = await connection.run_sync(
                lambda conn: inspect(conn).get_columns("identity_refresh_tokens")
            )
            assert len(columns) == 9
            assert {c["name"] for c in columns if c["nullable"]} == {
                "consumed_at",
                "replaced_by_id",
            }
            assert all(c["default"] is None for c in columns)
            assert str(next(c["type"] for c in columns if c["name"] == "verifier")) == "BYTEA"
            checks = await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("identity_refresh_tokens")
            )
            assert len(checks) == 6
            indexes = await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("identity_refresh_tokens")
            )
            assert len(indexes) == 1 and indexes[0]["unique"]
            assert indexes[0]["column_names"] == ["session_id"]
            fks = await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("identity_refresh_tokens")
            )
            assert len(fks) == 2 and all(fk["options"]["ondelete"] == "RESTRICT" for fk in fks)

    asyncio.run(run())
