"""HTTP commit/rollback evidence against the guarded isolated PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.pool import AsyncAdaptedQueuePool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database
from fleetlink.main import create_app
from fleetlink.modules.identity.application.refresh_authentication import (
    ProvisionalRefresh,
    ProvisionalRefreshReuse,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    RefreshCredential,
    derive_refresh_verifier,
)
from fleetlink.modules.identity.application.session_ports import SessionConflict
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord, RefreshTokenStatus
from fleetlink.modules.identity.domain.user import User
from fleetlink.modules.identity.infrastructure.models import (
    AuthenticationSessionRecord,
    UserRecord,
)
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
    IdentityOperationDependency,
    IdentityServices,
)
from fleetlink.modules.identity.interface.http.errors import (
    AuthenticationDenied,
    IdentityRoute,
    identity_problem_responses,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
AT = CREATED + timedelta(minutes=1)
USER = User(UUID(int=1400), CREATED)
SESSION = AuthenticationSession(
    UUID(int=1401), USER.id, UUID(int=1402), CREATED, CREATED + timedelta(days=1)
)


@pytest.mark.parametrize(
    "mode", ["success", "rollback", "commit_failure", "reuse", "reuse_failure"]
)
def test_http_transaction_durability(
    settings: Settings, migrated_database: None, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    credential = RefreshCredential(UUID(int=1403), bytes(range(32)))
    original = RefreshTokenRecord(
        credential.candidate_id,
        SESSION.id,
        derive_refresh_verifier(credential),
        CREATED,
        CREATED + timedelta(hours=1),
    )
    app = create_app(settings.model_copy(update={"database_enabled": True}))
    router = APIRouter(route_class=IdentityRoute, responses=identity_problem_responses())
    returned: list[bool] = []

    @router.post("/fixture")
    async def fixture(operation: IdentityOperationDependency) -> dict[str, str]:
        async def work(services: IdentityServices) -> ProvisionalRefresh | ProvisionalRefreshReuse:
            result = await services.authenticate_refresh(credential.reveal(), at=AT)
            if mode == "rollback":
                raise SessionConflict("synthetic-private-state")
            if mode in {"commit_failure", "reuse_failure"}:
                # Existing deferred FK fails only at COMMIT, after the service returned.
                assert isinstance(services.tokens, SqlAlchemyRefreshTokenRepository)
                await services.tokens._session.execute(
                    text(
                        "UPDATE identity_refresh_tokens SET replaced_by_id = :missing "
                        "WHERE session_id = :owner AND status = 'consumed'"
                    ),
                    {"missing": UUID(int=1499), "owner": SESSION.id},
                )
            return result

        result = await operation.execute(work)
        returned.append(True)
        if isinstance(result, ProvisionalRefreshReuse):
            raise AuthenticationDenied()
        # Demonstrate safe projection only; no credential endpoint or issuance.
        return {"status": "ok"}

    app.include_router(router)
    with TestClient(app) as client:
        database: Database = app.state.database
        assert client.portal is not None

        async def seed() -> None:
            async with database.session() as session, session.begin():
                await SqlAlchemyUserRepository(session).add(USER)
                await SqlAlchemyAuthenticationSessionRepository(session).add(SESSION)
                await SqlAlchemyRefreshTokenRepository(session).add(original)
            if mode in {"reuse", "reuse_failure"}:

                async def rotate(services: IdentityServices) -> None:
                    await services.authenticate_refresh(credential.reveal(), at=AT)

                await IdentityOperation(database).execute(rotate)

        async def state() -> tuple[AuthenticationSession | None, list[RefreshTokenRecord]]:
            async with database.session() as session, session.begin():
                lineage = await SqlAlchemyAuthenticationSessionRepository(session).get(SESSION.id)
                ids = await session.scalars(
                    select(Record.id).where(Record.session_id == SESSION.id).order_by(Record.id)
                )
                tokens = SqlAlchemyRefreshTokenRepository(session)
                records = []
                for identifier in ids:
                    token = await tokens.get(identifier)
                    assert token is not None
                    records.append(token)
                return lineage, records

        async def cleanup() -> None:
            async with database.session() as session, session.begin():
                await session.execute(delete(Record).where(Record.session_id == SESSION.id))
                await session.execute(
                    delete(AuthenticationSessionRecord).where(
                        AuthenticationSessionRecord.id == SESSION.id
                    )
                )
                await session.execute(delete(UserRecord).where(UserRecord.id == USER.id))

        try:
            client.portal.call(seed)
            before = client.portal.call(state)
            response = client.post("/fixture")
            pool = database.engine.pool
            assert isinstance(pool, AsyncAdaptedQueuePool)
            assert pool.checkedout() == 0
            after = client.portal.call(state)
            if mode == "success":
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                assert returned == [True]
                lineage, records = after
                assert lineage is not None and lineage.version == 1
                assert len(records) == 2
                assert {token.status for token in records} == {
                    RefreshTokenStatus.CURRENT,
                    RefreshTokenStatus.CONSUMED,
                }
                assert all(token.expires_at == original.expires_at for token in records)
            elif mode == "reuse":
                assert response.status_code == 401
                assert returned == [True]
                assert after[0] is not None and after[0].status is SessionStatus.REVOKED
                assert after[1] == before[1]
            else:
                assert response.status_code == (401 if mode == "rollback" else 500)
                assert returned == []
                assert after == before
            for value in (
                credential.reveal(),
                str(USER.id),
                "synthetic-private-state",
                "replaced_by_id",
            ):
                assert value not in response.text + caplog.text
        finally:
            client.portal.call(cleanup)
