"""FL-013 real PostgreSQL evidence; only the guarded isolated database is used."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError
from fleetlink.modules.identity.application import refresh_authentication as service
from fleetlink.modules.identity.application.refresh_protocol import (
    IssuedRefreshCredential,
    RefreshCredential,
    derive_refresh_verifier,
    parse_refresh_credential,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import SessionConflict
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshSessionUnavailable,
    RefreshTokenRecord,
    RefreshTokenStatus,
)
from fleetlink.modules.identity.domain.user import PlatformRole, User
from fleetlink.modules.identity.infrastructure.models import AuthenticationSessionRecord, UserRecord
from fleetlink.modules.identity.infrastructure.models import RefreshTokenRecord as Record
from fleetlink.modules.identity.infrastructure.refresh_repository import (
    SqlAlchemyRefreshTokenRepository,
)
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
AT = CREATED + timedelta(minutes=1)
USER = User(UUID(int=1300), CREATED, roles=frozenset(PlatformRole))
SESSION = AuthenticationSession(
    UUID(int=1301), USER.id, UUID(int=1302), CREATED, CREATED + timedelta(days=1)
)
OTHER = replace(SESSION, id=UUID(int=1311), family_id=UUID(int=1312))


def credential() -> RefreshCredential:
    return RefreshCredential(UUID(int=1303), bytes(range(32)))


def initial_token() -> RefreshTokenRecord:
    value = credential()
    return RefreshTokenRecord(
        value.candidate_id,
        SESSION.id,
        derive_refresh_verifier(value),
        CREATED,
        CREATED + timedelta(hours=1),
    )


@asynccontextmanager
async def database_for_test(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.model_copy(update={"database_pool_size": 3}))
    try:
        async with database.session() as session, session.begin():
            await SqlAlchemyUserRepository(session).add(USER)
            sessions = SqlAlchemyAuthenticationSessionRepository(session)
            await sessions.add(SESSION)
            await sessions.add(OTHER)
            await SqlAlchemyRefreshTokenRepository(session).add(initial_token())
        yield database
    finally:
        try:
            async with database.session() as session, session.begin():
                await session.execute(
                    delete(Record).where(Record.session_id.in_([SESSION.id, OTHER.id]))
                )
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.user_id == USER.id
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id == USER.id))
        finally:
            await database.dispose()


async def refresh(
    session: AsyncSession,
    presented: object,
    *,
    sessions: SqlAlchemyAuthenticationSessionRepository | None = None,
) -> service.ProvisionalRefresh | service.ProvisionalRefreshReuse:
    return await service.authenticate_refresh(
        presented,
        at=AT,
        tokens=SqlAlchemyRefreshTokenRepository(session),
        sessions=sessions or SqlAlchemyAuthenticationSessionRepository(session),
        users=SqlAlchemyUserRepository(session),
    )


async def state(database: Database) -> tuple[AuthenticationSession, list[RefreshTokenRecord]]:
    async with database.session() as session, session.begin():
        lineage = await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
        assert lineage is not None
        ids = (
            await session.scalars(
                select(Record.id).where(Record.session_id == SESSION.id).order_by(Record.id)
            )
        ).all()
        records = []
        for identifier in ids:
            record = await SqlAlchemyRefreshTokenRepository(session).get(identifier)
            assert record is not None
            records.append(record)
        return lineage, records


async def committed_refresh(database: Database) -> service.ProvisionalRefresh:
    async with database.session() as session, session.begin():
        result = await refresh(session, credential().reveal())
        assert isinstance(result, service.ProvisionalRefresh)
    return result


def test_refresh_commit_evidence_and_sensitive_boundary(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            result = await committed_refresh(database)
            lineage, records = await state(database)
            assert lineage == replace(SESSION, version=1)
            assert len(records) == 2
            old = next(record for record in records if record.id == initial_token().id)
            new = next(record for record in records if record.id != old.id)
            assert old == replace(initial_token().consume(new.id, AT), version=1)
            assert new.status is RefreshTokenStatus.CURRENT and new.version == 0
            assert new.created_at == AT and new.expires_at == old.expires_at < SESSION.expires_at
            assert new.verifier == derive_refresh_verifier(
                parse_refresh_credential(result.reveal())
            )
            assert verify_refresh_credential(result.reveal(), new)
            async with database.session() as session, session.begin():
                # Inspect all persisted columns without emitting any material in assertions.
                rows = (await session.execute(select(Record.__table__))).all()
                for row in rows:
                    assert bool(result.reveal() not in str(tuple(row)))
                    assert bool(result.reveal().encode() not in tuple(row))
                assert (
                    await session.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0004_refresh_token_rotation"
                )

    asyncio.run(run())


@pytest.mark.parametrize("consumed", [False, True])
def test_wrong_secret_never_mutates(
    settings: Settings, migrated_database: None, consumed: bool
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            if consumed:
                await committed_refresh(database)
            before = await state(database)
            wrong = RefreshCredential(initial_token().id, bytes(reversed(range(32))))
            with pytest.raises(service.RefreshPossessionFailed):
                async with database.session() as session, session.begin():
                    await refresh(session, wrong.reveal())
            assert await state(database) == before

    asyncio.run(run())


def test_confirmed_reuse_revokes_only_own_family_preserving_evidence(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            current = await committed_refresh(database)
            active, before = await state(database)
            assert active.status is SessionStatus.ACTIVE
            async with database.session() as session, session.begin():
                result = await refresh(session, credential().reveal())
                assert type(result) is service.ProvisionalRefreshReuse
            revoked, records = await state(database)
            assert revoked == replace(active.revoke(), version=active.version + 1)
            assert records == before
            for _ in range(3):
                async with database.session() as session, session.begin():
                    result = await refresh(session, credential().reveal())
                    assert type(result) is service.ProvisionalRefreshReuse
                lineage, records = await state(database)
                assert lineage == revoked
                assert records == before
                async with database.session() as session, session.begin():
                    assert (
                        await SqlAlchemyAuthenticationSessionRepository(session).get(OTHER.id)
                        == OTHER
                    )
                    assert await SqlAlchemyUserRepository(session).get(USER.id) == USER
            with pytest.raises(RefreshSessionUnavailable):
                async with database.session() as session, session.begin():
                    await refresh(session, current.reveal())

    asyncio.run(run())


def test_two_current_refreshes_have_exactly_one_normal_winner(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            barrier = asyncio.Barrier(2)
            reads = 0

            class ConcurrentSessions(SqlAlchemyAuthenticationSessionRepository):
                async def get(self, session_id: UUID) -> AuthenticationSession | None:
                    nonlocal reads
                    snapshot = await super().get(session_id)
                    reads += 1
                    await barrier.wait()
                    return snapshot

            async def attempt() -> (
                service.ProvisionalRefresh | service.ProvisionalRefreshReuse | SessionConflict
            ):
                try:
                    async with database.session() as session, session.begin():
                        return await refresh(
                            session, credential().reveal(), sessions=ConcurrentSessions(session)
                        )
                except SessionConflict as error:
                    return error

            outcomes = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), timeout=15)
            assert reads == 2  # No reload/retry-to-success.
            assert sum(isinstance(outcome, SessionConflict) for outcome in outcomes) == 1
            winners = [
                outcome for outcome in outcomes if isinstance(outcome, service.ProvisionalRefresh)
            ]
            assert len(winners) == 1
            lineage, records = await state(database)
            assert lineage == replace(SESSION, version=1)
            assert len(records) == 2
            current = [record for record in records if record.status is RefreshTokenStatus.CURRENT]
            assert len(current) == 1
            assert verify_refresh_credential(winners[0].reveal(), current[0])
            consumed = next(
                record for record in records if record.status is RefreshTokenStatus.CONSUMED
            )
            assert consumed.replaced_by_id == current[0].id

    asyncio.run(run())


@pytest.mark.parametrize("first", ["either", "refresh", "revoke"])
def test_refresh_revocation_race_preserves_lineage(
    settings: Settings, migrated_database: None, first: str
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            barrier = asyncio.Barrier(2)
            committed = asyncio.Event()

            class ConcurrentSessions(SqlAlchemyAuthenticationSessionRepository):
                async def get(self, session_id: UUID) -> AuthenticationSession | None:
                    snapshot = await super().get(session_id)
                    await barrier.wait()
                    if first == "revoke":
                        await committed.wait()
                    return snapshot

            async def rotate() -> (
                service.ProvisionalRefresh | service.ProvisionalRefreshReuse | None
            ):
                try:
                    async with database.session() as session, session.begin():
                        result = await refresh(
                            session, credential().reveal(), sessions=ConcurrentSessions(session)
                        )
                    committed.set()
                    return result
                except SessionConflict:
                    return None

            async def revoke() -> AuthenticationSession | None:
                try:
                    async with database.session() as session, session.begin():
                        repository = SqlAlchemyAuthenticationSessionRepository(session)
                        snapshot = await repository.get(SESSION.id)
                        assert snapshot == SESSION
                        await barrier.wait()
                        if first == "refresh":
                            await committed.wait()
                        result = await repository.save(snapshot.revoke())
                    committed.set()
                    return result
                except SessionConflict:
                    return None

            rotated, revoked = await asyncio.wait_for(
                asyncio.gather(rotate(), revoke()), timeout=15
            )
            assert (rotated is None) != (revoked is None)
            lineage, records = await state(database)
            if revoked is not None:
                assert lineage == replace(SESSION.revoke(), version=1)
                assert records == [initial_token()]
                assert first != "refresh"
            else:
                assert isinstance(rotated, service.ProvisionalRefresh)
                assert lineage == replace(SESSION, version=1)
                assert len(records) == 2
                assert first != "revoke"
                # A subsequent deliberate revocation invalidates that replacement too.
                async with database.session() as session, session.begin():
                    await SqlAlchemyAuthenticationSessionRepository(session).save(lineage.revoke())
                with pytest.raises(RefreshSessionUnavailable):
                    async with database.session() as session, session.begin():
                        await refresh(session, rotated.reveal())

    asyncio.run(run())


def test_failed_replacement_insert_rolls_back_both_cas_writes(
    settings: Settings, migrated_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            current = await committed_refresh(database)
            before = await state(database)
            # Duplicate the retained old ID, forcing a real INSERT failure after both CAS writes.
            generated = IssuedRefreshCredential(credential(), derive_refresh_verifier(credential()))
            monkeypatch.setattr(service, "generate_refresh_credential", lambda: generated)
            with pytest.raises(DatabaseError) as caught:
                async with database.session() as session, session.begin():
                    await refresh(session, current.reveal())
            assert (
                str(caught.value)
                == "Database operation failed; verify connectivity and configuration"
            )
            assert await state(database) == before

    asyncio.run(run())


@pytest.mark.parametrize("replay", [False, True])
def test_caller_failure_rolls_back_provisional_result(
    settings: Settings, migrated_database: None, replay: bool
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            if replay:
                await committed_refresh(database)
            before = await state(database)
            with pytest.raises(RuntimeError, match="^Injected caller failure$"):
                async with database.session() as session, session.begin():
                    await refresh(session, credential().reveal())
                    assert await state(database) == before  # Still invisible to independent reader.
                    raise RuntimeError("Injected caller failure")
            assert await state(database) == before

    asyncio.run(run())


def test_replay_database_failure_rolls_back_revocation(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            await committed_refresh(database)
            before = await state(database)

            class FailedSessions(SqlAlchemyAuthenticationSessionRepository):
                async def save(self, snapshot: AuthenticationSession) -> AuthenticationSession:
                    saved = await super().save(snapshot)
                    # Fail at COMMIT after the service returns, via the existing deferred FK.
                    await self._session.execute(
                        text(
                            "UPDATE identity_refresh_tokens SET replaced_by_id = :missing "
                            "WHERE status = 'consumed' AND session_id = :owner"
                        ),
                        {"missing": UUID(int=1399), "owner": SESSION.id},
                    )
                    return saved

            with pytest.raises(DatabaseError):
                async with database.session() as session, session.begin():
                    result = await refresh(
                        session, credential().reveal(), sessions=FailedSessions(session)
                    )
                    assert isinstance(result, service.ProvisionalRefreshReuse)
            assert await state(database) == before

    asyncio.run(run())


def test_replay_revocation_conflict_never_reactivates_or_partially_commits(
    settings: Settings, migrated_database: None
) -> None:
    async def run() -> None:
        async with database_for_test(settings) as database:
            await committed_refresh(database)
            before_session, before_records = await state(database)

            class StaleSessions(SqlAlchemyAuthenticationSessionRepository):
                async def get(self, session_id: UUID) -> AuthenticationSession | None:
                    snapshot = await super().get(session_id)
                    assert snapshot is not None
                    async with database.session() as concurrent, concurrent.begin():
                        await SqlAlchemyAuthenticationSessionRepository(concurrent).save(
                            snapshot.revoke()
                        )
                    return snapshot

            with pytest.raises(SessionConflict):
                async with database.session() as session, session.begin():
                    await refresh(session, credential().reveal(), sessions=StaleSessions(session))
            lineage, records = await state(database)
            assert lineage == replace(before_session.revoke(), version=before_session.version + 1)
            assert records == before_records
            assert lineage.status is SessionStatus.REVOKED

    asyncio.run(run())
