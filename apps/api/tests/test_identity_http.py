"""Test-mounted HTTP routes only; no credential workflow is publicly installed."""

import asyncio
import io
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from anyio import CancelScope, sleep
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.core.config import Settings
from fleetlink.core.dependencies import get_database
from fleetlink.core.http import Problem
from fleetlink.core.logging import JsonFormatter, configure_logging
from fleetlink.infrastructure.database import Database
from fleetlink.main import create_app
from fleetlink.modules.identity.application.refresh_authentication import ProvisionalRefreshReuse
from fleetlink.modules.identity.application.refresh_protocol import (
    RefreshCredentialGenerationError,
    generate_refresh_credential,
)
from fleetlink.modules.identity.domain.refresh_token import InvalidRefreshRotation
from fleetlink.modules.identity.interface.http import dependencies
from fleetlink.modules.identity.interface.http.dependencies import (
    IdentityOperation,
    IdentityOperationDependency,
    IdentityServices,
    get_identity_operation,
)
from fleetlink.modules.identity.interface.http.errors import (
    AUTHENTICATION_FAILURES,
    AuthenticationDenied,
    AuthenticationProblem,
    IdentityRoute,
    identity_problem_responses,
)
from fleetlink.observability import Telemetry

SENSITIVE = "synthetic-private-input"


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class ProbeOutput(BaseModel):
    id: UUID


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("fleetlink")
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Database, AsyncMock, list[str]]]:
    database = Database(Settings())
    session = AsyncMock(spec=AsyncSession)
    session.begin = AsyncMock()
    events: list[str] = []
    for name in ("begin", "commit", "rollback", "close"):
        getattr(session, name).side_effect = lambda event=name: events.append(event)
    monkeypatch.setattr(database, "sessions", Mock(return_value=session))
    try:
        yield database, session, events
    finally:
        asyncio.run(database.dispose())


def mounted_app(
    database: Database,
    events: list[str],
    failure: Exception | None = None,
    *,
    reuse: bool = False,
    invalid_response: bool = False,
) -> FastAPI:
    app = create_app(Settings(database_enabled=False))
    app.dependency_overrides[get_database] = lambda: database
    router = APIRouter(route_class=IdentityRoute, responses=identity_problem_responses())

    @router.post("/fixture", response_model=ProbeOutput)
    async def fixture(
        body: ProbeInput,
        operation: IdentityOperationDependency,
        same: Annotated[IdentityOperation, Depends(get_identity_operation)],
    ) -> object:
        assert operation is same

        async def work(services: IdentityServices) -> ProbeOutput | ProvisionalRefreshReuse:
            events.append("work")
            if failure is not None:
                raise failure
            if reuse:
                return ProvisionalRefreshReuse()
            # Construct a validated safe projection before commit.
            return ProbeOutput(id=body.id)

        result = await operation.execute(work)
        events.append("returned")
        if isinstance(result, ProvisionalRefreshReuse):
            raise AuthenticationDenied()
        return {"id": SENSITIVE} if invalid_response else result

    app.include_router(router)
    return app


def test_success_commits_once_before_return_and_closes_each_request(
    resources: tuple[Database, AsyncMock, list[str]],
) -> None:
    database, session, events = resources
    with TestClient(mounted_app(database, events)) as client:
        for _ in range(2):
            response = client.post("/fixture", json={"id": str(UUID(int=14))})
            assert response.status_code == 200
            assert response.json() == {"id": str(UUID(int=14))}
    assert events == ["begin", "work", "commit", "close", "returned"] * 2
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("error", AUTHENTICATION_FAILURES)
def test_expected_errors_are_indistinguishable_and_rollback(
    resources: tuple[Database, AsyncMock, list[str]],
    logs: io.StringIO,
    error: type[Exception],
) -> None:
    database, session, events = resources
    with TestClient(mounted_app(database, events, error(SENSITIVE))) as client:
        response = client.post(
            "/fixture?private=" + SENSITIVE,
            json={"id": str(UUID(int=14))},
            headers={"Authorization": SENSITIVE, "Cookie": SENSITIVE, "X-Correlation-ID": "fl014"},
        )
    assert response.status_code == 401
    parsed = AuthenticationProblem.model_validate(response.json())
    assert parsed.correlation_id == "fl014"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-correlation-id"] == "fl014"
    assert events == ["begin", "work", "rollback", "close"]
    session.commit.assert_not_awaited()
    assert SENSITIVE not in response.text + logs.getvalue()
    assert "Traceback" not in logs.getvalue()


@pytest.mark.parametrize(
    "error", [RuntimeError, ValueError, InvalidRefreshRotation, RefreshCredentialGenerationError]
)
def test_unknown_errors_remain_sanitized_500(
    resources: tuple[Database, AsyncMock, list[str]], logs: io.StringIO, error: type[Exception]
) -> None:
    database, session, events = resources
    with TestClient(mounted_app(database, events, error(SENSITIVE))) as client:
        response = client.post("/fixture", json={"id": str(UUID(int=14))})
    assert response.status_code == 500
    assert Problem.model_validate(response.json()).code == "internal_error"
    assert events == ["begin", "work", "rollback", "close"]
    session.commit.assert_not_awaited()
    assert SENSITIVE not in response.text + logs.getvalue()


@pytest.mark.parametrize("stage", ["begin", "commit", "rollback", "close"])
def test_lifecycle_failures_never_return_success(
    resources: tuple[Database, AsyncMock, list[str]], logs: io.StringIO, stage: str
) -> None:
    database, session, events = resources

    async def fail() -> None:
        events.append(stage)
        raise OSError(SENSITIVE)

    getattr(session, stage).side_effect = fail
    failure = AuthenticationDenied(SENSITIVE) if stage == "rollback" else None
    with TestClient(mounted_app(database, events, failure)) as client:
        response = client.post("/fixture", json={"id": str(UUID(int=14))})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "returned" not in events
    assert events[-1] == "close"
    if stage == "commit":
        assert events == ["begin", "work", "commit", "rollback", "close"]
    assert SENSITIVE not in response.text + logs.getvalue()


@pytest.mark.parametrize(
    "body", [{}, {"id": SENSITIVE}, {"id": str(UUID(int=14)), "role": SENSITIVE}]
)
def test_validation_never_starts_transaction_or_reflects_inputs(
    resources: tuple[Database, AsyncMock, list[str]], logs: io.StringIO, body: dict[str, str]
) -> None:
    database, _, events = resources
    with TestClient(mounted_app(database, events)) as client:
        response = client.post("/fixture", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert events == []
    assert SENSITIVE not in response.text + logs.getvalue()


def test_response_validation_is_sanitized(
    resources: tuple[Database, AsyncMock, list[str]], logs: io.StringIO
) -> None:
    database, _, events = resources
    with TestClient(mounted_app(database, events, invalid_response=True)) as client:
        response = client.post("/fixture", json={"id": str(UUID(int=14))})
    assert response.status_code == 500
    assert SENSITIVE not in response.text + logs.getvalue()
    # Response generation after commit cannot undo committed work; document this limit.
    assert events == ["begin", "work", "commit", "close", "returned"]


def test_confirmed_reuse_denial_occurs_after_commit(
    resources: tuple[Database, AsyncMock, list[str]],
) -> None:
    database, session, events = resources
    with TestClient(mounted_app(database, events, reuse=True)) as client:
        response = client.post("/fixture", json={"id": str(UUID(int=14))})
    assert response.status_code == 401
    assert events == ["begin", "work", "commit", "close", "returned"]
    session.rollback.assert_not_awaited()


def test_ports_share_session_and_application_service_is_reused(
    resources: tuple[Database, AsyncMock, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    database, session, _ = resources
    service = AsyncMock(return_value=ProvisionalRefreshReuse())
    monkeypatch.setattr(dependencies, "authenticate_refresh", service)

    async def run() -> None:
        async def work(services: IdentityServices) -> ProvisionalRefreshReuse:
            assert vars(services.users)["_session"] is session
            assert vars(services.sessions)["_session"] is session
            assert vars(services.tokens)["_session"] is session
            at = datetime(2026, 1, 1, tzinfo=UTC)
            result = await services.authenticate_refresh(SENSITIVE, at=at)
            service.assert_awaited_once_with(
                SENSITIVE,
                at=at,
                users=services.users,
                sessions=services.sessions,
                tokens=services.tokens,
            )
            assert isinstance(result, ProvisionalRefreshReuse)
            return result

        operation = IdentityOperation(database)
        assert isinstance(await operation.execute(work), ProvisionalRefreshReuse)
        with pytest.raises(RuntimeError, match="already used"):
            await operation.execute(work)

    asyncio.run(run())


def test_cancellation_rolls_back_and_closes(
    resources: tuple[Database, AsyncMock, list[str]],
) -> None:
    database, _, events = resources

    async def run() -> None:
        async def work(services: IdentityServices) -> None:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await IdentityOperation(database).execute(work)

    asyncio.run(run())
    assert events == ["begin", "rollback", "close"]


def test_openapi_contract_and_no_production_routes(
    resources: tuple[Database, AsyncMock, list[str]],
) -> None:
    database, _, events = resources
    schema = mounted_app(database, events).openapi()
    operation = schema["paths"]["/fixture"]["post"]
    assert "requestBody" in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ProbeOutput"
    }
    for status, model in ((401, AuthenticationProblem), (422, Problem), (500, Problem)):
        content = operation["responses"][str(status)]["content"]
        assert content == {"application/problem+json": {"schema": model.model_json_schema()}}
    app = create_app(Settings(database_enabled=False))
    assert set(app.openapi()["paths"]) == {"/health", "/ready"}
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {
            "status": "ready",
            "checks": {"application": "ready"},
            "dependency_checks": "not_configured",
        }
        for path in ("login", "register", "refresh", "logout", "validate"):
            assert client.post("/api/v1/identity/" + path).status_code == 404


def test_disabled_database_dependency_uses_existing_500_contract(logs: io.StringIO) -> None:
    app = create_app(Settings(database_enabled=False))
    router = APIRouter(route_class=IdentityRoute)

    @router.get("/fixture")
    async def fixture(operation: IdentityOperationDependency) -> None:
        pytest.fail("Dependency must fail before handler")

    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/fixture")
    assert response.status_code == 500
    assert response.json()["detail"] == "An unexpected error occurred."
    assert "disabled or outside" not in response.text + logs.getvalue()


def test_generated_credential_and_verifier_never_enter_http_diagnostics(
    resources: tuple[Database, AsyncMock, list[str]], logs: io.StringIO
) -> None:
    database, _, events = resources
    issued = generate_refresh_credential()
    wire = issued.credential.reveal()
    evidence = issued.verifier.value.hex()
    exporter = InMemorySpanExporter()

    def telemetry(config: Settings, service: str) -> Telemetry:
        return Telemetry(config, service, span_exporter=exporter)

    app = create_app(Settings(database_enabled=False), telemetry_factory=telemetry)
    app.dependency_overrides[get_database] = lambda: database
    router = APIRouter(route_class=IdentityRoute)

    @router.post("/fixture")
    async def fixture(body: ProbeInput, operation: IdentityOperationDependency) -> None:
        async def fail(services: IdentityServices) -> None:
            raise RuntimeError(wire + evidence)

        await operation.execute(fail)

    app.include_router(router)
    with TestClient(app) as client:
        responses = [
            client.post("/fixture", json={"id": wire}),
            client.post(
                "/fixture?credential=" + wire,
                json={"id": str(UUID(int=14))},
                headers={"Authorization": "Bearer " + wire, "Cookie": "fixture=" + wire},
            ),
        ]
    assert [response.status_code for response in responses] == [422, 500]
    assert events == ["begin", "rollback", "close"]
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert all(not span.events for span in spans)
    diagnostic = logs.getvalue() + "".join(response.text for response in responses)
    diagnostic += repr([(span.name, span.attributes, span.status.description) for span in spans])
    assert wire not in diagnostic
    assert evidence not in diagnostic


def test_anyio_level_cancellation_shields_rollback_and_close(
    resources: tuple[Database, AsyncMock, list[str]],
) -> None:
    database, session, events = resources

    async def rollback() -> None:
        await sleep(0)
        events.append("rollback")

    async def close() -> None:
        await sleep(0)
        events.append("close")

    session.rollback.side_effect = rollback
    session.close.side_effect = close

    async def run() -> None:
        with CancelScope() as scope:

            async def work(services: IdentityServices) -> None:
                scope.cancel()
                await sleep(0)

            await IdentityOperation(database).execute(work)
            pytest.fail("Cancelled operation must not return a result")

    asyncio.run(run())
    assert events == ["begin", "rollback", "close"]
