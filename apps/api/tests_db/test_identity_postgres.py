"""Real PostgreSQL Identity persistence; guarded fixtures never use development DBs."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import DateTime, delete, inspect, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.pool import AsyncAdaptedQueuePool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application.ports import (
    IdentityConflict,
    UserNotFound,
    UserRepository,
)
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User
from fleetlink.modules.identity.infrastructure.models import UserRecord
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository

USER_ID = UUID("00000000-0000-4000-8000-000000000009")
MISSING_ID = UUID("00000000-0000-4000-8000-000000000010")
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


@asynccontextmanager
async def database_for_test(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 3}))
    try:
        yield database
    finally:
        try:
            async with database.session() as session, session.begin():
                await session.execute(delete(UserRecord).where(UserRecord.id == USER_ID))
        finally:
            await database.dispose()


def test_schema_constraints_and_types(settings: Settings, migrated_database: None) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database, database.connection() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            assert {"identity_users", "identity_user_roles", "spatial_ref_sys"} <= set(tables)
            columns = await connection.run_sync(
                lambda conn: inspect(conn).get_columns("identity_users")
            )
            assert {column["name"] for column in columns} == {
                "id",
                "status",
                "created_at",
                "version",
            }
            assert all(not column["nullable"] for column in columns)
            assert str(next(c["type"] for c in columns if c["name"] == "id")) == "UUID"
            timestamp_type = next(c["type"] for c in columns if c["name"] == "created_at")
            assert isinstance(timestamp_type, DateTime) and timestamp_type.timezone
            for table, expected_pk, checks in (
                (
                    "identity_users",
                    ["id"],
                    {"ck_identity_users_status", "ck_identity_users_version"},
                ),
                ("identity_user_roles", ["user_id", "role"], {"ck_identity_user_roles_role"}),
            ):
                pk = await connection.run_sync(
                    lambda conn, name: inspect(conn).get_pk_constraint(name), table
                )
                assert pk["constrained_columns"] == expected_pk
                constraints = await connection.run_sync(
                    lambda conn, name: inspect(conn).get_check_constraints(name), table
                )
                assert {constraint["name"] for constraint in constraints} == checks
                assert (
                    await connection.run_sync(
                        lambda conn, name: inspect(conn).get_indexes(name), table
                    )
                    == []
                )
            foreign_keys = await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("identity_user_roles")
            )
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "identity_users"
            assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"

    asyncio.run(run())


def test_round_trip_roles_updates_and_independent_sessions(
    settings: Settings,
    migrated_database: None,
) -> None:
    async def run() -> None:
        user = User(USER_ID, CREATED, roles=frozenset(PlatformRole))
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                repository: UserRepository = SqlAlchemyUserRepository(session)
                await repository.add(user)
                assert await repository.get(USER_ID) == user
            async with database.session() as session, session.begin():
                await session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
                repository = SqlAlchemyUserRepository(session)
                loaded = await repository.get(USER_ID)
                assert loaded == user and loaded is not user
                assert loaded.created_at.tzinfo is UTC
                changed = loaded.remove_role(PlatformRole.MERCHANT).with_status(
                    AccountStatus.SUSPENDED
                )
                saved = await repository.save(changed)
                assert saved.version == 1
                assert await repository.get(USER_ID) == saved
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER_ID) == saved
                await session.execute(delete(UserRecord).where(UserRecord.id == USER_ID))
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM identity_user_roles WHERE user_id = :id"),
                        {"id": USER_ID},
                    )
                    == 0
                )

    asyncio.run(run())


def test_missing_identity_and_immutable_creation_metadata(
    settings: Settings,
    migrated_database: None,
) -> None:
    async def run() -> None:
        user = User(USER_ID, CREATED)
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                repository = SqlAlchemyUserRepository(session)
                assert await repository.get(MISSING_ID) is None
                await repository.add(user)
            with pytest.raises(UserNotFound, match="does not exist"):
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).save(User(MISSING_ID, CREATED))
            with pytest.raises(IdentityConflict):
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).save(
                        replace(user, created_at=CREATED + timedelta(seconds=1))
                    )
            with pytest.raises(IdentityConflict):
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).add(
                        replace(user, id=MISSING_ID, version=1)
                    )
            with pytest.raises(InvalidRequestError, match="Autobegin"):
                async with database.session() as session:
                    await SqlAlchemyUserRepository(session).get(USER_ID)

    asyncio.run(run())


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO identity_user_roles VALUES (:id, 'customer')",
        "INSERT INTO identity_user_roles VALUES (:missing, 'rider')",
        "INSERT INTO identity_user_roles VALUES (:id, 'superuser')",
        "UPDATE identity_users SET status = 'invalid' WHERE id = :id",
        "UPDATE identity_users SET status = NULL WHERE id = :id",
        "UPDATE identity_users SET version = -1 WHERE id = :id",
        "INSERT INTO identity_users SELECT * FROM identity_users WHERE id = :id",
    ],
)
def test_database_enforces_invariants_and_sanitizes_errors(
    settings: Settings,
    migrated_database: None,
    statement: str,
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(DatabaseError) as caught:
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).add(
                        User(USER_ID, CREATED).assign_role(PlatformRole.CUSTOMER)
                    )
                    await session.execute(text(statement), {"id": USER_ID, "missing": MISSING_ID})
            assert str(USER_ID) not in str(caught.value)
            assert statement not in str(caught.value)
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER_ID) is None

    asyncio.run(run())


def test_uncommitted_changes_are_isolated_and_failure_rolls_back(
    settings: Settings,
    migrated_database: None,
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as writer, writer.begin():
                    await SqlAlchemyUserRepository(writer).add(User(USER_ID, CREATED))
                    async with database.session() as reader, reader.begin():
                        assert await SqlAlchemyUserRepository(reader).get(USER_ID) is None
                    raise RuntimeError("injected")
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER_ID) is None

    asyncio.run(run())


def test_cancelled_identity_transaction_releases_connection_and_rolls_back(
    settings: Settings,
    migrated_database: None,
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            written = asyncio.Event()

            async def work() -> None:
                async with database.session() as session, session.begin():
                    await SqlAlchemyUserRepository(session).add(
                        User(USER_ID, CREATED).assign_role(PlatformRole.RIDER)
                    )
                    written.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(work())
            try:
                await asyncio.wait_for(written.wait(), timeout=10)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
            assert database.engine.pool.checkedout() == 0
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER_ID) is None

    asyncio.run(run())


def test_competing_role_changes_reject_stale_snapshot(
    settings: Settings,
    migrated_database: None,
) -> None:
    async def run() -> None:
        user = User(USER_ID, CREATED)
        async with database_for_test(settings) as database:
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(user)
            ready = asyncio.Barrier(2)

            async def change(role: PlatformRole) -> User | None:
                try:
                    async with database.session() as session, session.begin():
                        repository = SqlAlchemyUserRepository(session)
                        loaded = await repository.get(USER_ID)
                        assert loaded == user
                        await ready.wait()
                        return await repository.save(loaded.assign_role(role))
                except IdentityConflict:
                    return None

            results = await asyncio.wait_for(
                asyncio.gather(
                    change(PlatformRole.MERCHANT),
                    change(PlatformRole.RIDER),
                ),
                timeout=15,
            )
            winners = [result for result in results if result is not None]
            assert len(winners) == 1
            assert winners[0].version == 1
            assert len(winners[0].roles) == 1
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER_ID) == winners[0]

    asyncio.run(run())
