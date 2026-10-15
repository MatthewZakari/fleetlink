"""FL-016 real lock ordering; only the guarded fresh PostgreSQL test database."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import AsyncAdaptedQueuePool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application.refresh_authentication import (
    ProvisionalRefresh,
    ProvisionalRefreshReuse,
    RefreshAccountUnavailable,
)
from fleetlink.modules.identity.application.session_establishment import (
    ProvisionalSessionEstablishment,
    SessionAccountUnavailable,
    SessionLifetimePolicy,
    TrustedAuthenticationAssertion,
    establish_initial_session,
)
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User
from fleetlink.modules.identity.infrastructure.models import (
    AuthenticationSessionRecord,
    RefreshTokenRecord,
    UserRecord,
)
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.interface.http.dependencies import (
    IdentityOperation,
    IdentityServices,
)

AT = datetime(2026, 1, 1, tzinfo=UTC)
USER = User(UUID(int=1600), AT - timedelta(days=1))
OTHER = replace(USER, id=UUID(int=1610))
POLICY = SessionLifetimePolicy(timedelta(hours=2), timedelta(hours=1))
Mode = Literal["establish", "refresh"]
Result = ProvisionalSessionEstablishment | ProvisionalRefresh | ProvisionalRefreshReuse


async def establish(ports: IdentityServices, user: User = USER) -> ProvisionalSessionEstablishment:
    return await establish_initial_session(
        TrustedAuthenticationAssertion(user.id, AT),
        session_id=UUID(int=user.id.int + 1),
        family_id=UUID(int=user.id.int + 2),
        created_at=AT,
        session_expires_at=AT + timedelta(hours=2),
        refresh_expires_at=AT + timedelta(hours=1),
        lifetime_policy=POLICY,
        users=ports.users,
        sessions=ports.sessions,
        tokens=ports.tokens,
    )


def session_for(ports: IdentityServices) -> AsyncSession:
    assert isinstance(ports.users, SqlAlchemyUserRepository)
    return ports.users._session


async def pid(session: AsyncSession) -> int:
    value = await session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(value, int)
    return value


async def wait_for_block(database: Database, waiter: int, holder: int) -> None:
    # Poll PostgreSQL's actual wait graph, not elapsed sleep or task scheduling.
    async with asyncio.timeout(5), database.session() as observer, observer.begin():
        while True:
            blocked = await observer.scalar(
                text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                {"holder": holder, "waiter": waiter},
            )
            if blocked:
                return


@asynccontextmanager
async def scenario(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 5}))
    try:
        async with database.session() as session, session.begin():
            assert await session.scalar(text("SHOW transaction_isolation")) == "read committed"
            for user in (USER, OTHER):
                await SqlAlchemyUserRepository(session).add(user)
        yield database
        assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
        assert database.engine.pool.checkedout() == 0
    finally:
        try:
            async with database.session() as session, session.begin():
                owners = (USER.id, OTHER.id)
                sessions = select(AuthenticationSessionRecord.id).where(
                    AuthenticationSessionRecord.user_id.in_(owners)
                )
                await session.execute(
                    delete(RefreshTokenRecord).where(RefreshTokenRecord.session_id.in_(sessions))
                )
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.user_id.in_(owners)
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id.in_(owners)))
        finally:
            await database.dispose()


async def evidence(database: Database) -> tuple[tuple[object, ...], ...]:
    """Safe state projection for rollback comparison; no bearer/verifier diagnostics."""
    async with database.session() as session, session.begin():
        sessions = select(AuthenticationSessionRecord.id).where(
            AuthenticationSessionRecord.user_id == USER.id
        )
        rows = await session.execute(
            select(
                AuthenticationSessionRecord.id,
                AuthenticationSessionRecord.version,
                RefreshTokenRecord.id,
                RefreshTokenRecord.status,
                RefreshTokenRecord.version,
            )
            .outerjoin(RefreshTokenRecord)
            .where(AuthenticationSessionRecord.id.in_(sessions))
            .order_by(RefreshTokenRecord.id)
        )
        return tuple(tuple(row) for row in rows)


async def initial(database: Database, mode: Mode) -> ProvisionalSessionEstablishment | None:
    return await IdentityOperation(database).execute(establish) if mode == "refresh" else None


async def authenticate(
    ports: IdentityServices, seed: ProvisionalSessionEstablishment | None
) -> Result:
    if seed is None:
        return await establish(ports)
    return await ports.authenticate_refresh(seed.reveal(), at=AT + timedelta(minutes=1))


@pytest.mark.parametrize("mode", ["establish", "refresh"])
@pytest.mark.parametrize("status", [AccountStatus.SUSPENDED, AccountStatus.DISABLED])
def test_status_writer_first_refreshes_waiting_eligibility(
    settings: Settings, migrated_database: None, mode: Mode, status: AccountStatus
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            seed = await initial(database, mode)
            before = await evidence(database)
            waiting: asyncio.Queue[int] = asyncio.Queue()
            async with asyncio.TaskGroup() as tasks:
                async with database.session() as writer, writer.begin():
                    holder = await pid(writer)
                    changed = replace(USER.with_status(status), roles=frozenset(PlatformRole))
                    await SqlAlchemyUserRepository(writer).save(changed)

                    async def attempt() -> None:
                        async def work(ports: IdentityServices) -> Result:
                            session = session_for(ports)
                            # Retain a stale ORM identity as well as an ordinary snapshot.
                            cached = await session.get(UserRecord, USER.id)
                            assert cached is not None and cached.status == "active"
                            assert await ports.users.get(USER.id) == USER
                            await waiting.put(await pid(session))
                            return await authenticate(ports, seed)

                        with pytest.raises((SessionAccountUnavailable, RefreshAccountUnavailable)):
                            await IdentityOperation(database).execute(work)

                    task = tasks.create_task(attempt())
                    waiter = await waiting.get()
                    await wait_for_block(database, waiter, holder)
                    assert not task.done()
                # Writer commits here; authentication must observe the new status.
            assert await evidence(database) == before
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) == replace(
                    changed, version=1
                )

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("mode", ["establish", "refresh"])
@pytest.mark.parametrize("completion", ["commit", "rollback", "cancel"])
@pytest.mark.parametrize("status", [AccountStatus.SUSPENDED, AccountStatus.DISABLED])
def test_authentication_first_holds_until_completion(
    settings: Settings,
    migrated_database: None,
    mode: Mode,
    completion: str,
    status: AccountStatus,
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            seed = await initial(database, mode)
            before = await evidence(database)
            holding: asyncio.Queue[int] = asyncio.Queue()
            waiting: asyncio.Queue[int] = asyncio.Queue()
            release = asyncio.Event()

            async def authenticate_and_hold() -> None:
                async def work(ports: IdentityServices) -> Result:
                    result = await authenticate(ports, seed)
                    await holding.put(await pid(session_for(ports)))
                    await release.wait()
                    if completion == "rollback":
                        raise RuntimeError("Synthetic caller failure")
                    return result

                if completion == "rollback":
                    with pytest.raises(RuntimeError, match="Synthetic caller failure"):
                        await IdentityOperation(database).execute(work)
                else:
                    await IdentityOperation(database).execute(work)

            async def change_status() -> None:
                async with database.session() as session, session.begin():
                    await waiting.put(await pid(session))
                    await SqlAlchemyUserRepository(session).save(USER.with_status(status))

            async with asyncio.TaskGroup() as tasks:
                authentication = tasks.create_task(authenticate_and_hold())
                holder = await holding.get()
                writer = tasks.create_task(change_status())
                await wait_for_block(database, await waiting.get(), holder)
                assert not writer.done()

                # A different user's complete operation succeeds while this writer waits.
                async def other(ports: IdentityServices) -> ProvisionalSessionEstablishment:
                    return await establish(ports, OTHER)

                await asyncio.wait_for(IdentityOperation(database).execute(other), 3)
                assert await evidence(database) == before  # Uncommitted effects remain invisible.
                if completion == "cancel":
                    authentication.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await authentication
                else:
                    release.set()
            after = await evidence(database)
            assert (after != before) is (completion == "commit")
            async with database.session() as session, session.begin():
                # Only the status writer increments version; authentication never does.
                assert await SqlAlchemyUserRepository(session).get(USER.id) == replace(
                    USER.with_status(status), version=1
                )
                assert await SqlAlchemyUserRepository(session).get(OTHER.id) == OTHER

    asyncio.run(asyncio.wait_for(run(), 15))


def test_repository_fresh_snapshot_roles_and_explicit_transaction(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            async with database.session() as session:
                with pytest.raises(InvalidRequestError, match="Autobegin"):
                    await SqlAlchemyUserRepository(session).get_for_authentication(USER.id)
            async with database.session() as reader, reader.begin():
                repository = SqlAlchemyUserRepository(reader)
                cached = await reader.get(UserRecord, USER.id)
                assert cached is not None
                old = await repository.get(USER.id)
                changed = replace(
                    USER, roles=frozenset(PlatformRole), status=AccountStatus.DISABLED
                )
                async with database.session() as writer, writer.begin():
                    await SqlAlchemyUserRepository(writer).save(changed)
                fresh = await repository.get_for_authentication(USER.id)
                assert fresh == replace(changed, version=1)
                assert old == USER  # Detached snapshots remain immutable.
                assert cached.status == "disabled"  # Identity map was refreshed too.
                assert await repository.get_for_authentication(UUID(int=1699)) is None
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) == fresh

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("mode", ["establish", "refresh"])
@pytest.mark.parametrize("failure", ["cancel", "timeout"])
def test_waiting_authentication_cancel_or_lock_timeout_is_atomic(
    settings: Settings, migrated_database: None, mode: Mode, failure: str
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            seed = await initial(database, mode)
            before = await evidence(database)
            waiting: asyncio.Queue[int] = asyncio.Queue()
            async with asyncio.TaskGroup() as tasks:
                async with database.session() as writer, writer.begin():
                    holder = await pid(writer)
                    await SqlAlchemyUserRepository(writer).save(
                        USER.with_status(AccountStatus.DISABLED)
                    )

                    async def attempt() -> None:
                        async def work(ports: IdentityServices) -> Result:
                            session = session_for(ports)
                            if failure == "timeout":
                                # Test-only bound, no production setting/default added.
                                await session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                            await waiting.put(await pid(session))
                            return await authenticate(ports, seed)

                        if failure == "timeout":
                            with pytest.raises(DatabaseError) as caught:
                                await IdentityOperation(database).execute(work)
                            assert str(caught.value) == (
                                "Database operation failed; verify connectivity and configuration"
                            )
                        else:
                            await IdentityOperation(database).execute(work)

                    task = tasks.create_task(attempt())
                    await wait_for_block(database, await waiting.get(), holder)
                    if failure == "cancel":
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        await asyncio.wait_for(task, 5)
            assert await evidence(database) == before
            # No leaked waiter/lock: a fresh coordinated transaction can complete.
            async with database.session() as session, session.begin():
                user = await SqlAlchemyUserRepository(session).get_for_authentication(USER.id)
                assert user is not None and user.status is AccountStatus.DISABLED

    asyncio.run(asyncio.wait_for(run(), 15))


def test_confirmed_reuse_does_not_wait_for_account_writer(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            seed = await IdentityOperation(database).execute(establish)

            async def refresh(ports: IdentityServices) -> Result:
                return await authenticate(ports, seed)

            assert isinstance(
                await IdentityOperation(database).execute(refresh), ProvisionalRefresh
            )
            async with database.session() as writer, writer.begin():
                await SqlAlchemyUserRepository(writer).save(
                    USER.with_status(AccountStatus.DISABLED)
                )
                result = await asyncio.wait_for(IdentityOperation(database).execute(refresh), 3)
                assert isinstance(result, ProvisionalRefreshReuse)
            async with database.session() as session, session.begin():
                lineage = await session.get(AuthenticationSessionRecord, UUID(int=1601))
                assert lineage is not None and lineage.status == "revoked"

    asyncio.run(asyncio.wait_for(run(), 15))
