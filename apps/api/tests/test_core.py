"""Infrastructure-free contracts and deterministic ASGI failure/concurrency tests."""

import asyncio
import io
import json
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from fleetlink.core.config import Settings
from fleetlink.core.dependencies import get_correlation_id, get_settings
from fleetlink.core.http import Problem, RequestContextMiddleware
from fleetlink.core.logging import (
    JsonFormatter,
    RequestLevelFilter,
    configure_logging,
    correlation_id,
    request_log_level,
)
from fleetlink.core.readiness import Readiness, get_readiness
from fleetlink.main import create_app


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEETLINK_ENVIRONMENT", raising=False)
    monkeypatch.delenv("FLEETLINK_LOG_LEVEL", raising=False)


def test_settings_defaults_and_immutability(clean_env: None) -> None:
    settings = Settings()
    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production"])
def test_environment_selection(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("FLEETLINK_ENVIRONMENT", environment)
    monkeypatch.setenv("FLEETLINK_LOG_LEVEL", "WARNING")
    assert Settings().environment == environment
    assert Settings().log_level == "WARNING"


@pytest.mark.parametrize("key", ["ENVIRONMENT", "LOG_LEVEL"])
def test_invalid_settings_hide_input(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(f"FLEETLINK_{key}", "synthetic-sensitive-value")
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert "synthetic-sensitive-value" not in str(caught.value)


def test_factory_isolation_and_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(environment="test", log_level="DEBUG")
    first = create_app(settings)
    second = create_app(Settings(environment="production", log_level="ERROR"))
    assert first.state.settings is settings
    assert first.state.readiness is not second.state.readiness
    monkeypatch.setenv("FLEETLINK_ENVIRONMENT", "invalid-after-construction")

    @first.get("/fixture")
    async def fixture(
        config: Annotated[Settings, Depends(get_settings)],
        request_id: Annotated[str, Depends(get_correlation_id)],
    ) -> dict[str, str]:
        return {"environment": config.environment, "correlation_id": request_id}

    with TestClient(first) as client:
        assert not second.state.readiness.initialized
        response = client.get("/fixture", headers={"X-Correlation-ID": "fixture-id"})
        assert response.json() == {"environment": "test", "correlation_id": "fixture-id"}
        first.dependency_overrides[get_settings] = lambda: Settings(environment="staging")
        assert client.get("/fixture").json()["environment"] == "staging"
        first.dependency_overrides[get_readiness] = lambda: Readiness()
        assert client.get("/ready").status_code == 503
    assert not first.state.readiness.initialized
    assert not second.dependency_overrides


def test_readiness_before_and_after_lifespan() -> None:
    app = create_app(Settings(environment="test"))
    client = TestClient(app)
    assert client.get("/ready").status_code == 503
    with client:
        assert client.get("/ready").status_code == 200
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"application": "not_ready"},
        "dependency_checks": "not_configured",
    }
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/health", "/ready", "/missing", "/openapi.json", "/failure"])
def test_security_headers(path: str) -> None:
    app = create_app()

    @app.get("/failure")
    async def fail() -> None:
        raise RuntimeError("synthetic-secret")

    with TestClient(app) as client:
        response = client.get(path, headers={"Origin": "https://untrusted.example"})
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers
        assert "synthetic-secret" not in response.text
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


def test_openapi_stable_and_matches_errors() -> None:
    first, second = create_app(), create_app()
    assert first.openapi() == second.openapi()
    schema = first.openapi()
    assert set(schema["paths"]) == {"/health", "/ready"}
    for path in schema["paths"].values():
        responses = path["get"]["responses"]
        for status in ("404", "405", "422", "500"):
            assert set(responses[status]["content"]) == {"application/problem+json"}
            assert responses[status]["content"]["application/problem+json"]["schema"] == (
                Problem.model_json_schema()
            )
    with TestClient(first) as client:
        Problem.model_validate(client.get("/absent?secret=synthetic-secret").json())


def scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "method": "GET", "path": "/", "headers": headers or []}


async def receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.parametrize("value", [b"a" * 64, b"A.0_-", b"a"])
def test_correlation_boundaries(value: bytes) -> None:
    async def run() -> None:
        messages: list[Message] = []

        async def send(message: Message) -> None:
            messages.append(message)

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        await RequestContextMiddleware(app)(scope([(b"x-correlation-id", value)]), receive, send)
        assert dict(messages[0]["headers"])[b"x-correlation-id"] == value

    asyncio.run(run())


@pytest.mark.parametrize("value", [b"\xff", b"a\r\nb", b"a,b", b"-a", b"a\x00"])
def test_raw_malformed_headers(value: bytes) -> None:
    async def run() -> None:
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            UUID(scope["state"]["correlation_id"])

        async def send(message: Message) -> None:
            pass

        await RequestContextMiddleware(app)(scope([(b"x-correlation-id", value)]), receive, send)
        assert correlation_id.get() is None

    asyncio.run(run())


def test_concurrent_context_and_log_isolation() -> None:
    async def run() -> None:
        barrier = asyncio.Barrier(2)
        observed: list[tuple[str | None, int]] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await barrier.wait()
            observed.append((correlation_id.get(), request_log_level.get()))
            assert correlation_id.get() == scope["state"]["correlation_id"]

        async def send(message: Message) -> None:
            pass

        await asyncio.gather(
            RequestContextMiddleware(app, "DEBUG")(
                scope([(b"x-correlation-id", b"first")]), receive, send
            ),
            RequestContextMiddleware(app, "ERROR")(
                scope([(b"x-correlation-id", b"second")]), receive, send
            ),
        )
        assert set(observed) == {("first", logging.DEBUG), ("second", logging.ERROR)}
        assert correlation_id.get() is None
        assert request_log_level.get() == logging.INFO

    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_failure_after_response_start_never_resends(failure: type[BaseException]) -> None:
    async def run() -> None:
        messages: list[Message] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            raise failure("synthetic-secret")

        async def send(message: Message) -> None:
            messages.append(message)

        with pytest.raises(failure):
            await RequestContextMiddleware(app)(scope(), receive, send)
        assert [message["type"] for message in messages] == [
            "http.response.start",
            "http.response.body",
        ]
        assert correlation_id.get() is None

    asyncio.run(run())


def test_structured_failure_logging() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestLevelFilter())
    logger = logging.getLogger("fleetlink")
    configure_logging()
    original_handlers = logger.handlers[:]
    logger.handlers = [handler]
    try:
        app = create_app(Settings(log_level="INFO"))

        @app.get("/fixture")
        async def fail() -> None:
            logging.getLogger("fleetlink.fixture").info("fixture_event")
            raise ValueError("synthetic-secret")

        with TestClient(app) as client:
            client.get(
                "/fixture?token=synthetic-secret",
                headers={"X-Correlation-ID": "log-id", "Authorization": "Bearer synthetic-secret"},
            )
        payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [item["event"] for item in payloads] == [
            "fixture_event",
            "request_failed",
            "request_completed",
        ]
        for item in payloads:
            assert item["correlation_id"] == "log-id"
            offset = datetime.fromisoformat(item["timestamp"]).utcoffset()
            assert offset is not None and offset.total_seconds() == 0
            assert set(item) <= {
                "timestamp",
                "level",
                "service",
                "event",
                "correlation_id",
                "status_code",
                "duration_ms",
                "error_type",
            }
        assert payloads[-1]["status_code"] == 500
        assert payloads[-1]["duration_ms"] >= 0
        assert "synthetic-secret" not in stream.getvalue()
        assert "Traceback" not in stream.getvalue()
    finally:
        logger.handlers = original_handlers


def test_logging_setup_idempotent_and_factory_has_no_logging_side_effects() -> None:
    configure_logging()
    logger = logging.getLogger("fleetlink")
    handlers = logger.handlers[:]
    create_app(Settings(log_level="CRITICAL"))
    configure_logging()
    assert logger.handlers == handlers


@pytest.mark.parametrize("level, emitted", [(logging.DEBUG, True), (logging.ERROR, False)])
def test_request_log_filter(level: int, emitted: bool) -> None:
    token = request_log_level.set(level)
    try:
        record = logging.LogRecord("fleetlink", logging.INFO, "", 0, "event", (), None)
        assert RequestLevelFilter().filter(record) is emitted
    finally:
        request_log_level.reset(token)


def test_failed_initialization_never_marks_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_logging_setup() -> None:
        raise RuntimeError("synthetic-startup-failure")

    monkeypatch.setattr("fleetlink.main.configure_logging", fail_logging_setup)
    app = create_app()
    with pytest.raises(RuntimeError, match="synthetic-startup-failure"), TestClient(app):
        pytest.fail("Startup must fail before serving requests")
    assert not app.state.readiness.initialized


def test_lifespan_exception_resets_readiness() -> None:
    async def run() -> None:
        app = create_app()
        with pytest.raises(RuntimeError, match="synthetic-lifecycle-failure"):
            async with app.router.lifespan_context(app):
                assert app.state.readiness.initialized
                raise RuntimeError("synthetic-lifecycle-failure")
        assert not app.state.readiness.initialized

    asyncio.run(run())
