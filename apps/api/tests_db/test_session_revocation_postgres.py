"""FL-017 real transactions, races and failure boundaries in the isolated database."""

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
from fleetlink.modules.identity.application.refresh_authentication import (
    ProvisionalRefresh,
    ProvisionalRefreshReuse,
    RefreshPossessionFailed,
    authenticate_refresh,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    RefreshCredential,
    derive_refresh_verifier,
)
from fleetlink.modules.identity.application.session_ports import SessionConflict
from fleetlink.modules.identity.application.session_revocation import (
    ProvisionalSessionRevocation,
    revoke_session_by_refresh_credential,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshSessionUnavailable,
    RefreshTokenRecord,
)
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User
from fleetlink.modules.identity.infrastructure.models import AuthenticationSessionRecord, UserRecord
from fleetlink.modules.identity.infrastructure.models import RefreshTokenRecord as TokenRow
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
USER = User(UUID(int=1700), AT, roles=frozenset(PlatformRole))
OTHER_USER = replace(USER, id=UUID(int=1710))
SESSION = AuthenticationSession(
    UUID(int=1701), USER.id, UUID(int=1702), AT, AT + timedelta(hours=2)
)
SIBLING = replace(SESSION, id=UUID(int=1711), family_id=UUID(int=1712))
OTHER = replace(SESSION, id=UUID(int=1721), family_id=UUID(int=1722), user_id=OTHER_USER.id)


def credential() -> RefreshCredential:
    return RefreshCredential(UUID(int=1703), bytes(range(32)))


@asynccontextmanager
async def scenario(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 5}))
    try:
        async with database.session() as session, session.begin():
            users = SqlAlchemyUserRepository(session)
            await users.add(USER)
            await users.add(OTHER_USER)
            sessions = SqlAlchemyAuthenticationSessionRepository(session)
            for lineage in (SESSION, SIBLING, OTHER):
                await sessions.add(lineage)
            await SqlAlchemyRefreshTokenRepository(session).add(
                RefreshTokenRecord(
                    credential().candidate_id,
                    SESSION.id,
                    derive_refresh_verifier(credential()),
                    AT,
                    AT + timedelta(hours=1),
                )
            )
        yield database
        assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
        assert database.engine.pool.checkedout() == 0
    finally:
        try:
            async with database.session() as session, session.begin():
                ids = (SESSION.id, SIBLING.id, OTHER.id)
                await session.execute(delete(TokenRow).where(TokenRow.session_id.in_(ids)))
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.id.in_(ids)
                    )
                )
                await session.execute(
                    delete(UserRecord).where(UserRecord.id.in_((USER.id, OTHER_USER.id)))
                )
        finally:
            await database.dispose()


async def terminate(ports: IdentityServices) -> ProvisionalSessionRevocation:
    return await revoke_session_by_refresh_credential(
        credential().reveal(), tokens=ports.tokens, sessions=ports.sessions
    )


async def refresh(ports: IdentityServices) -> ProvisionalRefresh | ProvisionalRefreshReuse:
    return await ports.authenticate_refresh(credential().reveal(), at=AT + timedelta(minutes=1))


async def state(database: Database) -> tuple[AuthenticationSession, tuple[RefreshTokenRecord, ...]]:
    async with database.session() as session, session.begin():
        sessions = SqlAlchemyAuthenticationSessionRepository(session)
        lineage = await sessions.get(SESSION.id)
        assert lineage is not None
        assert await sessions.get(SIBLING.id) == SIBLING
        assert await sessions.get(OTHER.id) == OTHER
        assert await SqlAlchemyUserRepository(session).get(OTHER_USER.id) == OTHER_USER
        ids = (
            await session.scalars(
                select(TokenRow.id).where(TokenRow.session_id == SESSION.id).order_by(TokenRow.id)
            )
        ).all()
        records = []
        for identifier in ids:
            record = await SqlAlchemyRefreshTokenRepository(session).get(identifier)
            assert record is not None
            records.append(record)
        return lineage, tuple(records)


@pytest.mark.parametrize("consumed", [False, True])
def test_committed_repeat_is_scoped_and_preserves_evidence(
    settings: Settings, migrated_database: None, consumed: bool
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            if consumed:
                await IdentityOperation(database).execute(refresh)
            before, evidence = await state(database)
            for _ in range(3):
                assert (
                    await IdentityOperation(database).execute(terminate)
                    == ProvisionalSessionRevocation()
                )
                after, records = await state(database)
                assert after == replace(before.revoke(), version=before.version + 1)
                assert records == evidence
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) == USER
            if consumed:
                assert isinstance(
                    await IdentityOperation(database).execute(refresh), ProvisionalRefreshReuse
                )
                assert await state(database) == (after, evidence)
            else:
                with pytest.raises(RefreshSessionUnavailable):
                    await IdentityOperation(database).execute(refresh)

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("consumed", [False, True])
def test_forged_credential_has_no_effect(
    settings: Settings, migrated_database: None, consumed: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            if consumed:
                await IdentityOperation(database).execute(refresh)
            before = await state(database)
            wrong = RefreshCredential(credential().candidate_id, bytes(reversed(range(32))))

            async def work(ports: IdentityServices) -> ProvisionalSessionRevocation:
                return await revoke_session_by_refresh_credential(
                    wrong.reveal(), tokens=ports.tokens, sessions=ports.sessions
                )

            with pytest.raises(RefreshPossessionFailed) as caught:
                await IdentityOperation(database).execute(work)
            assert await state(database) == before
            output = caplog.text + str(caught.value)
            assert bool(wrong.reveal() not in output)
            assert bool(str(derive_refresh_verifier(credential()).value) not in output)

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("first", ["refresh", "revoke"])
@pytest.mark.parametrize("reuse", [False, True])
def test_competing_refresh_or_reuse_and_revocation(
    settings: Settings, migrated_database: None, first: str, reuse: bool
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            if reuse:
                await IdentityOperation(database).execute(refresh)
            before, evidence = await state(database)
            barrier = asyncio.Barrier(2)
            committed = asyncio.Event()
            reads: list[str] = []

            class CoordinatedSessions(SqlAlchemyAuthenticationSessionRepository):
                def __init__(self, session: AsyncSession, actor: str) -> None:
                    super().__init__(session)
                    self.actor = actor

                async def get(self, identifier: UUID) -> AuthenticationSession | None:
                    snapshot = await super().get(identifier)
                    reads.append(self.actor)
                    await barrier.wait()
                    if self.actor != first:
                        await committed.wait()
                    return snapshot

            async def attempt(actor: str) -> object:
                try:
                    async with database.session() as session, session.begin():
                        sessions = CoordinatedSessions(session, actor)
                        tokens = SqlAlchemyRefreshTokenRepository(session)
                        result: object
                        if actor == "revoke":
                            result = await revoke_session_by_refresh_credential(
                                credential().reveal(), sessions=sessions, tokens=tokens
                            )
                        else:
                            result = await authenticate_refresh(
                                credential().reveal(),
                                at=AT + timedelta(minutes=1),
                                sessions=sessions,
                                tokens=tokens,
                                users=SqlAlchemyUserRepository(session),
                            )
                    committed.set()
                    return result
                except SessionConflict as error:
                    return error

            refreshed, revoked = await asyncio.gather(attempt("refresh"), attempt("revoke"))
            assert sorted(reads) == ["refresh", "revoke"]  # No automatic reload/retry.
            winner, loser = (refreshed, revoked) if first == "refresh" else (revoked, refreshed)
            assert isinstance(loser, SessionConflict)
            after, records = await state(database)
            if first == "refresh" and not reuse:
                assert isinstance(winner, ProvisionalRefresh)
                assert after == replace(before, version=before.version + 1)
                assert len(records) == len(evidence) + 1
                # A fresh deliberate termination with retained historical evidence works.
                await IdentityOperation(database).execute(terminate)

                async def replacement(ports: IdentityServices) -> object:
                    return await ports.authenticate_refresh(
                        winner.reveal(), at=AT + timedelta(minutes=2)
                    )

                with pytest.raises(RefreshSessionUnavailable):
                    await IdentityOperation(database).execute(replacement)
            else:
                assert isinstance(winner, (ProvisionalSessionRevocation, ProvisionalRefreshReuse))
                assert after == replace(before.revoke(), version=before.version + 1)
                assert records == evidence

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("failure", ["caller", "commit", "cancel"])
def test_failure_rolls_back_and_releases_connections(
    settings: Settings, migrated_database: None, failure: str
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            await IdentityOperation(database).execute(refresh)
            before = await state(database)
            written = asyncio.Event()
            hold = asyncio.Event()

            async def work(ports: IdentityServices) -> ProvisionalSessionRevocation:
                result = await terminate(ports)
                assert await state(database) == before  # Independent transaction cannot see it.
                if failure == "caller":
                    raise RuntimeError("Synthetic caller failure")
                if failure == "commit":
                    assert isinstance(ports.tokens, SqlAlchemyRefreshTokenRepository)
                    await ports.tokens._session.execute(
                        text(
                            "UPDATE identity_refresh_tokens SET replaced_by_id = :missing "
                            "WHERE id = :id"
                        ),
                        {"missing": UUID(int=1799), "id": credential().candidate_id},
                    )
                else:
                    written.set()
                    await hold.wait()
                return result

            if failure == "cancel":
                task = asyncio.create_task(IdentityOperation(database).execute(work))
                await written.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(DatabaseError if failure == "commit" else RuntimeError):
                    await IdentityOperation(database).execute(work)
            assert await state(database) == before
            await IdentityOperation(database).execute(terminate)  # No retained lock.

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("stage", ["acknowledgment", "close"])
def test_uncertain_completion_returns_no_result(
    settings: Settings, migrated_database: None, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            before, evidence = await state(database)
            original = AsyncSession.commit if stage == "acknowledgment" else AsyncSession.close
            calls = 0

            async def fail_after_success(session: AsyncSession) -> None:
                nonlocal calls
                calls += 1
                await original(session)
                raise OSError("Synthetic private completion failure")

            with monkeypatch.context() as patch:
                patch.setattr(
                    AsyncSession,
                    "commit" if stage == "acknowledgment" else "close",
                    fail_after_success,
                )
                with pytest.raises(DatabaseError) as caught:
                    await IdentityOperation(database).execute(terminate)
            assert calls == 1
            assert "Synthetic private" not in str(caught.value)
            assert await state(database) == (
                replace(before.revoke(), version=before.version + 1),
                evidence,
            )
            # Injected post-commit exception models uncertainty, not a network-fault test.

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("status", [AccountStatus.SUSPENDED, AccountStatus.DISABLED])
def test_account_writer_does_not_block_termination(
    settings: Settings, migrated_database: None, status: AccountStatus
) -> None:
    async def run() -> None:
        async with scenario(settings) as database:
            async with database.session() as writer, writer.begin():
                changed = await SqlAlchemyUserRepository(writer).save(USER.with_status(status))
                # Writer demonstrably holds an uncommitted UPDATE; completion must precede release.
                await asyncio.wait_for(IdentityOperation(database).execute(terminate), 3)
                assert (await state(database))[0].status is SessionStatus.REVOKED
            async with database.session() as session, session.begin():
                assert await SqlAlchemyUserRepository(session).get(USER.id) == changed

    asyncio.run(asyncio.wait_for(run(), 15))


def test_sql_diagnostics_exclude_credential_and_evidence(
    settings: Settings, migrated_database: None, caplog: pytest.LogCaptureFixture
) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from fleetlink.observability import Telemetry
    from fleetlink.observability.database import DatabaseInstrumentation

    async def run() -> None:
        async with scenario(settings) as database:
            exporter = InMemorySpanExporter()
            runtime = Telemetry(settings, "fleetlink-api", span_exporter=exporter)
            instrumentation = DatabaseInstrumentation(database.engine.sync_engine, runtime)
            try:
                with runtime.operation("integration"):
                    result = await IdentityOperation(database).execute(terminate)

                    async def fail(ports: IdentityServices) -> ProvisionalSessionRevocation:
                        completed = await terminate(ports)
                        # A duplicate evidence insert exercises sanitized real driver errors.
                        await ports.tokens.add(
                            RefreshTokenRecord(
                                credential().candidate_id,
                                SESSION.id,
                                derive_refresh_verifier(credential()),
                                AT,
                                AT + timedelta(hours=1),
                            )
                        )
                        return completed

                    with pytest.raises(DatabaseError) as caught:
                        await IdentityOperation(database).execute(fail)
                spans = exporter.get_finished_spans()
                assert any(span.name == "postgresql.query" for span in spans)
                assert all(not span.events for span in spans)
                diagnostic = caplog.text + str(caught.value) + repr(result)
                diagnostic += repr(
                    [(span.name, span.attributes, span.status.description) for span in spans]
                )
                for sensitive in (
                    credential().reveal(),
                    credential().reveal().rsplit(".", 1)[1],
                    repr(derive_refresh_verifier(credential()).value),
                    derive_refresh_verifier(credential()).value.hex(),
                    repr(SESSION),
                ):
                    assert bool(sensitive not in diagnostic)
            finally:
                instrumentation.close()
                runtime.shutdown()

    asyncio.run(asyncio.wait_for(run(), 15))
