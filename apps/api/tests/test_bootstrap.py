import json
import logging
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fleetlink.core.config import Settings
from fleetlink.core.logging import JsonFormatter, correlation_id
from fleetlink.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(environment="test"))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_technical_endpoints(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    UUID(response.headers["x-correlation-id"])
    if path == "/health":
        assert response.json() == {"status": "ok"}
    else:
        assert response.json() == {
            "status": "ready",
            "checks": {"application": "ready"},
            "dependency_checks": "not_configured",
        }


def test_openapi_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/health", "/ready"}
    for path, model in [("/health", "HealthResponse"), ("/ready", "ReadinessResponse")]:
        response = schema["paths"][path]["get"]["responses"]["200"]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith(model)
    assert "503" in schema["paths"]["/ready"]["get"]["responses"]


def test_correlation_is_preserved_and_isolated(client: TestClient) -> None:
    assert (
        client.get("/health", headers={"X-Correlation-ID": "request-123"}).headers[
            "x-correlation-id"
        ]
        == "request-123"
    )
    first = client.get("/health").headers["x-correlation-id"]
    second = client.get("/health").headers["x-correlation-id"]
    assert first != second
    assert correlation_id.get() is None


@pytest.mark.parametrize("value", ["", "x" * 65, "contains spaces", "<script>"])
def test_invalid_correlation_id_is_replaced(client: TestClient, value: str) -> None:
    response = client.get("/health", headers={"X-Correlation-ID": value})
    UUID(response.headers["x-correlation-id"])
    assert response.headers["x-correlation-id"] != value


def test_duplicate_correlation_headers_are_replaced(client: TestClient) -> None:
    response = client.get("/health", headers=[("X-Correlation-ID", "a"), ("X-Correlation-ID", "b")])
    UUID(response.headers["x-correlation-id"])


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("get", "/missing", 404),
        ("post", "/health", 405),
    ],
)
def test_http_error_contract(client: TestClient, method: str, path: str, status: int) -> None:
    response = client.request(method, path, headers={"X-Correlation-ID": "test-error"})
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == f"http_{status}"
    assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    assert set(response.json()) == {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "code",
        "correlation_id",
    }
    if status == 405:
        assert "GET" in response.headers["allow"]


def test_unexpected_error_is_sanitized(app: FastAPI) -> None:
    @app.get("/test-failure")
    async def fail() -> None:
        raise RuntimeError("sensitive-value")

    with TestClient(app) as client:
        response = client.get("/test-failure")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    assert "sensitive-value" not in response.text


def test_validation_error_is_sanitized(app: FastAPI) -> None:
    @app.get("/test-validation")
    async def validate(count: int) -> dict[str, int]:
        return {"count": count}

    with TestClient(app) as client:
        response = client.get("/test-validation?count=sensitive-value")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "sensitive-value" not in response.text


def test_readiness_follows_lifecycle(app: FastAPI) -> None:
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        app.state.readiness.initialized = False
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"application": "not_ready"}
        assert client.get("/health").status_code == 200
    assert app.state.readiness.initialized is False


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEETLINK_ENVIRONMENT", "production")
    assert Settings().environment == "production"
    monkeypatch.setenv("FLEETLINK_LOG_LEVEL", "invalid")
    with pytest.raises(ValidationError):
        Settings()


def test_json_logging_does_not_serialize_secrets() -> None:
    record = logging.LogRecord("fleetlink", logging.INFO, "", 0, "request_completed", (), None)
    record.secret = "do-not-log"
    token = correlation_id.set("test-log")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        correlation_id.reset(token)
    assert payload["correlation_id"] == "test-log"
    assert payload["timestamp"].endswith("Z")
    assert "do-not-log" not in json.dumps(payload)
