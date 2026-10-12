"""FL-015 durability on the guarded isolated PostgreSQL database; no HTTP routes."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import AsyncAdaptedQueuePool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application import session_establishment as service
from fleetlink.modules.identity.application.refresh_authentication import ProvisionalRefresh
from fleetlink.modules.identity.application.refresh_protocol import (
    IssuedRefreshCredential,
    generate_refresh_credential,
    verify_refresh_credential,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord, RefreshTokenStatus
from fleetlink.modules.identity.domain.user import AccountStatus, User
from fleetlink.modules.identity.infrastructure.models import AuthenticationSessionRecord, UserRecord
from fleetlink.modules.identity.infrastructure.models import RefreshTokenRecord as Record
from fleetlink.modules.identity.infrastructure.refresh_repository import (
    SqlAlchemyRefreshTokenRepository,
)
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)
from fleetlink.modules.identity.interface.http.dependencies import (
    IdentityOperation,
    IdentityServices,
)

AT = datetime(2026, 1, 1, tzinfo=UTC)
USER = User(UUID(int=1500), AT - timedelta(days=1))
SESSION_ID = UUID(int=1501)
FAMILY_ID = UUID(int=1502)
POLICY = service.SessionLifetimePolicy(timedelta(hours=2), timedelta(hours=1))


@asynccontextmanager
async def database_for_test(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 3}))
    try:
        async with database.session() as session, session.begin():
            await SqlAlchemyUserRepository(session).add(USER)
        yield database
        assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
        assert database.engine.pool.checkedout() == 0
    finally:
        try:
            async with database.session() as session, session.begin():
                ids = select(AuthenticationSessionRecord.id).where(
                    AuthenticationSessionRecord.user_id == USER.id
                )
                await session.execute(delete(Record).where(Record.session_id.in_(ids)))
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.user_id == USER.id
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id == USER.id))
        finally:
            await database.dispose()


async def establish(
    services: IdentityServices, *, session_id: UUID = SESSION_ID, family_id: UUID = FAMILY_ID
) -> service.ProvisionalSessionEstablishment:
    return await service.establish_initial_session(
        service.TrustedAuthenticationAssertion(USER.id, AT),
        session_id=session_id,
        family_id=family_id,
        created_at=AT,
        session_expires_at=AT + POLICY.max_session_lifetime,
        refresh_expires_at=AT + POLICY.max_refresh_lifetime,
        lifetime_policy=POLICY,
        users=services.users,
        sessions=services.sessions,
        tokens=services.tokens,
    )


async def state(database: Database) -> tuple[list[AuthenticationSession], list[RefreshTokenRecord]]:
    async with database.session() as session, session.begin():
        session_ids = (
            await session.scalars(
                select(AuthenticationSessionRecord.id)
                .where(AuthenticationSessionRecord.user_id == USER.id)
                .order_by(AuthenticationSessionRecord.id)
            )
        ).all()
        sessions = []
        for identifier in session_ids:
            snapshot = await SqlAlchemyAuthenticationSessionRepository(session).get(identifier)
            assert snapshot is not None
            sessions.append(snapshot)
        token_ids = (
            await session.scalars(
                select(Record.id).where(Record.session_id.in_(session_ids)).order_by(Record.id)
            )
        ).all()
        tokens = []
        for identifier in token_ids:
            token = await SqlAlchemyRefreshTokenRepository(session).get(identifier)
            assert token is not None
            tokens.append(token)
        return sessions, tokens


async def suspend(database: Database) -> None:
    async with database.session() as session, session.begin():
        await SqlAlchemyUserRepository(session).save(USER.with_status(AccountStatus.SUSPENDED))


def test_committed_initial_evidence_and_fl013_compatibility(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            result = await IdentityOperation(database).execute(establish)
            sessions, tokens = await state(database)
            assert len(sessions) == len(tokens) == 1
            assert sessions[0].status is SessionStatus.ACTIVE and sessions[0].version == 0
            assert tokens[0].status is RefreshTokenStatus.CURRENT and tokens[0].version == 0
            assert tokens[0].session_id == sessions[0].id == result.session_id
            assert tokens[0].created_at == sessions[0].created_at == AT
            assert verify_refresh_credential(result.reveal(), tokens[0])
            async with database.session() as session, session.begin():
                rows = (
                    await session.execute(
                        select(Record.__table__).where(Record.session_id == SESSION_ID)
                    )
                ).all()
                for row in rows:
                    assert bool(result.reveal() not in str(tuple(row)))
                    assert bool(result.reveal().encode() not in tuple(row))
                assert await SqlAlchemyUserRepository(session).get(USER.id) == USER

            async def refresh(ports: IdentityServices) -> ProvisionalRefresh:
                rotated = await ports.authenticate_refresh(
                    result.reveal(), at=AT + timedelta(minutes=1)
                )
                assert isinstance(rotated, ProvisionalRefresh)
                return rotated

            rotated = await IdentityOperation(database).execute(refresh)
            sessions, tokens = await state(database)
            assert sessions[0].version == 1 and len(tokens) == 2
            assert all(
                token.expires_at == result.refresh_expires_at < result.session_expires_at
                for token in tokens
            )
            current = next(token for token in tokens if token.status is RefreshTokenStatus.CURRENT)
            assert verify_refresh_credential(rotated.reveal(), current)

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["session", "token", "commit"])
def test_rollback_removes_both_records(
    settings: Settings, migrated_database: None, stage: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:

            class FailTokens(SqlAlchemyRefreshTokenRepository):
                async def add(self, token: RefreshTokenRecord) -> None:
                    # The session was flushed; fail before token insertion.
                    assert (
                        await self._session.get(AuthenticationSessionRecord, SESSION_ID) is not None
                    )
                    raise RuntimeError("Injected insertion failure")

            async def work(ports: IdentityServices) -> service.ProvisionalSessionEstablishment:
                assert isinstance(ports.tokens, SqlAlchemyRefreshTokenRepository)
                if stage == "session":
                    ports = replace(ports, tokens=FailTokens(ports.tokens._session))
                result = await establish(ports)
                assert await state(database) == (
                    [],
                    [],
                )  # Independent reader cannot see either row.
                if stage == "token":
                    raise RuntimeError("Injected caller failure")
                assert isinstance(ports.tokens, SqlAlchemyRefreshTokenRepository)
                # Row checks pass, but the existing deferred replacement FK fails at COMMIT.
                await ports.tokens._session.execute(
                    text(
                        "UPDATE identity_refresh_tokens SET status = 'consumed', "
                        "consumed_at = :at, "
                        "replaced_by_id = :missing WHERE session_id = :owner"
                    ),
                    {"at": AT, "missing": UUID(int=1599), "owner": SESSION_ID},
                )
                return result

            with pytest.raises(DatabaseError if stage == "commit" else RuntimeError):
                await IdentityOperation(database).execute(work)
            assert await state(database) == ([], [])

    asyncio.run(run())


@pytest.mark.parametrize("duplicate", ["session", "family", "token"])
def test_duplicate_identifiers_preserve_existing_rows(
    settings: Settings, migrated_database: None, monkeypatch: pytest.MonkeyPatch, duplicate: str
) -> None:
    issued = generate_refresh_credential()
    calls = 0

    def generate() -> IssuedRefreshCredential:
        nonlocal calls
        calls += 1
        return issued

    monkeypatch.setattr(service, "generate_refresh_credential", generate)

    async def run() -> None:
        async with database_for_test(settings) as database:
            await IdentityOperation(database).execute(establish)
            before = await state(database)

            async def again(ports: IdentityServices) -> service.ProvisionalSessionEstablishment:
                return await establish(
                    ports,
                    session_id=SESSION_ID if duplicate == "session" else UUID(int=1511),
                    family_id=FAMILY_ID if duplicate == "family" else UUID(int=1512),
                )

            with pytest.raises(DatabaseError) as caught:
                await IdentityOperation(database).execute(again)
            assert (
                str(caught.value)
                == "Database operation failed; verify connectivity and configuration"
            )
            assert await state(database) == before
            assert calls == 2  # No regeneration/retry after collision.

    asyncio.run(run())


def test_cancelled_operation_rolls_back_and_closes(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            written = asyncio.Event()
            blocked = asyncio.Event()

            async def work(ports: IdentityServices) -> service.ProvisionalSessionEstablishment:
                result = await establish(ports)
                written.set()
                await blocked.wait()
                return result

            task = asyncio.create_task(IdentityOperation(database).execute(work))
            await asyncio.wait_for(written.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await state(database) == ([], [])

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["acknowledgment", "close"])
def test_post_commit_failure_returns_no_result_but_rows_survive(
    settings: Settings, migrated_database: None, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            original = AsyncSession.commit if stage == "acknowledgment" else AsyncSession.close
            calls = 0

            async def fail_after_success(session: AsyncSession) -> None:
                nonlocal calls
                calls += 1
                await original(session)
                raise OSError("Synthetic private acknowledgment failure")

            # Simulation of uncertainty, not a real dropped PostgreSQL network acknowledgment.
            with monkeypatch.context() as patch:
                patch.setattr(
                    AsyncSession,
                    "commit" if stage == "acknowledgment" else "close",
                    fail_after_success,
                )
                with pytest.raises(DatabaseError) as caught:
                    await IdentityOperation(database).execute(establish)
            assert "Synthetic private" not in str(caught.value)
            assert calls == 1
            sessions, tokens = await state(database)
            assert len(sessions) == len(tokens) == 1

    asyncio.run(run())


@pytest.mark.parametrize("collision", ["session", "family", "distinct"])
def test_concurrent_establishment(
    settings: Settings, migrated_database: None, collision: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            barrier = asyncio.Barrier(2)
            reads = 0

            class ConcurrentUsers(SqlAlchemyUserRepository):
                async def get(self, user_id: UUID) -> User | None:
                    nonlocal reads
                    user = await super().get(user_id)
                    reads += 1
                    await barrier.wait()
                    return user

            async def attempt(
                second: bool,
            ) -> service.ProvisionalSessionEstablishment | DatabaseError:
                async def work(ports: IdentityServices) -> service.ProvisionalSessionEstablishment:
                    assert isinstance(ports.users, SqlAlchemyUserRepository)
                    ports = replace(ports, users=ConcurrentUsers(ports.users._session))
                    return await establish(
                        ports,
                        session_id=UUID(int=1511)
                        if second and collision != "session"
                        else SESSION_ID,
                        family_id=UUID(int=1512) if second and collision != "family" else FAMILY_ID,
                    )

                try:
                    return await IdentityOperation(database).execute(work)
                except DatabaseError as error:
                    return error

            outcomes = await asyncio.wait_for(asyncio.gather(attempt(False), attempt(True)), 15)
            expected = 2 if collision == "distinct" else 1
            assert (
                sum(
                    isinstance(outcome, service.ProvisionalSessionEstablishment)
                    for outcome in outcomes
                )
                == expected
            )
            assert reads == 2
            sessions, tokens = await state(database)
            assert len(sessions) == len(tokens) == expected

    asyncio.run(run())


@pytest.mark.parametrize("when", ["before", "after"])
def test_suspension_snapshot_boundary(
    settings: Settings, migrated_database: None, when: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            if when == "before":
                await suspend(database)

            class SuspendingUsers(SqlAlchemyUserRepository):
                async def get(self, user_id: UUID) -> User | None:
                    snapshot = await super().get(user_id)
                    # Independent transaction commits suspension after this read, before insertion.
                    if when == "after":
                        await suspend(database)
                    return snapshot

            async def work(ports: IdentityServices) -> service.ProvisionalSessionEstablishment:
                assert isinstance(ports.users, SqlAlchemyUserRepository)
                return await establish(replace(ports, users=SuspendingUsers(ports.users._session)))

            if when == "before":
                with pytest.raises(service.SessionAccountUnavailable):
                    await IdentityOperation(database).execute(work)
                assert await state(database) == ([], [])
            else:
                await IdentityOperation(database).execute(work)
                sessions, tokens = await state(database)
                assert len(sessions) == len(tokens) == 1
            async with database.session() as session, session.begin():
                user = await SqlAlchemyUserRepository(session).get(USER.id)
                assert user is not None and user.status is AccountStatus.SUSPENDED

    asyncio.run(run())


def test_generated_material_excluded_from_database_diagnostics(
    settings: Settings,
    migrated_database: None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from fleetlink.observability import Telemetry
    from fleetlink.observability.database import DatabaseInstrumentation

    issued = generate_refresh_credential()
    monkeypatch.setattr(service, "generate_refresh_credential", lambda: issued)

    async def run() -> None:
        async with database_for_test(settings) as database:
            exporter = InMemorySpanExporter()
            runtime = Telemetry(settings, "fleetlink-api", span_exporter=exporter)
            instrumentation = DatabaseInstrumentation(database.engine.sync_engine, runtime)
            try:
                with runtime.operation("integration"):
                    result = await IdentityOperation(database).execute(establish)

                    # Duplicate token insertion sends sensitive verifier bytes to PostgreSQL,
                    # exercising driver-error sanitization and SQL telemetry on a failure too.
                    async def collision(
                        ports: IdentityServices,
                    ) -> service.ProvisionalSessionEstablishment:
                        return await establish(
                            ports, session_id=UUID(int=1511), family_id=UUID(int=1512)
                        )

                    with pytest.raises(DatabaseError) as caught:
                        await IdentityOperation(database).execute(collision)
                spans = exporter.get_finished_spans()
                assert any(span.name == "postgresql.query" for span in spans)
                assert all(not span.events for span in spans)
                diagnostic = caplog.text + str(caught.value) + repr(result)
                diagnostic += repr(
                    [(span.name, span.attributes, span.status.description) for span in spans]
                )
                for sensitive in (
                    issued.credential.reveal(),
                    issued.credential.reveal().rsplit(".", 1)[1],
                    repr(issued.verifier.value),
                    issued.verifier.value.hex(),
                ):
                    assert bool(sensitive not in diagnostic)
            finally:
                instrumentation.close()
                runtime.shutdown()

    asyncio.run(run())
