"""FL-008 observable-boundary regressions; generated fake sentinels, no services."""

import asyncio
import builtins
import json
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError as DatabaseOperationalError

from fleetlink.core.config import Settings
from fleetlink.core.logging import JsonFormatter
from fleetlink.core.secrets import SECRET_FIELDS, EnvironmentSecretSource
from fleetlink.infrastructure.broker import BrokerError, create_celery, safe_broker_errors
from fleetlink.infrastructure.database import DatabaseError, _safe_errors
from fleetlink.infrastructure.redis import RedisError, TechnicalRedis
from fleetlink.infrastructure.tasks import InvalidProbe, validate_payload
from fleetlink.main import create_app
from fleetlink.observability import Telemetry
from fleetlink.worker import WorkerFormatter


@pytest.fixture
def sentinel() -> str:
    return f"FAKE-FL008-DO-NOT-USE-{uuid4()}"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear only configuration inputs; never dump environment contents.
    import os

    for name in tuple(os.environ):
        if name.startswith(("FLEETLINK_", "OTEL_", "CELERY_")):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("field", sorted(SECRET_FIELDS))
def test_secret_representation(field: str, sentinel: str) -> None:
    # OTLP origins are constrained independently of their sensitive classification.
    value = "http://localhost:4318" if field == "otel_exporter_otlp_endpoint" else sentinel
    settings = Settings(secret_source=None, **{field: SecretStr(value)})
    secret = getattr(settings, field)
    assert value not in repr(secret)
    assert value not in str(secret)
    assert value not in repr(settings)
    assert value not in settings.model_dump_json()
    assert value not in json.dumps(settings.diagnostic_configuration())
    with pytest.raises(ValidationError):
        settings.environment = "production"


@pytest.mark.parametrize("field", sorted(SECRET_FIELDS))
def test_invalid_secret_validation(field: str, sentinel: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(secret_source=None, **{field: {"rejected": sentinel}})
    assert field in str(caught.value)
    for surface in (
        str(caught.value),
        repr(caught.value),
        str(caught.value.errors()),
        caught.value.json(),
    ):
        assert sentinel not in surface


def test_invalid_endpoint_validation(sentinel: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(otel_exporter_otlp_endpoint=SecretStr(f"https://user:{sentinel}@localhost"))
    assert "OTLP endpoint" in str(caught.value)
    assert sentinel not in caught.value.json()


def test_nonsecret_diagnostics() -> None:
    settings = Settings(postgres_port=5433, database_pool_size=7)
    assert settings.diagnostic_configuration()["database_pool_size"] == 7
    assert "postgres_port=5433" in repr(settings)
    with pytest.raises(ValidationError) as caught:
        Settings(postgres_port=0)
    assert "postgres_port" in str(caught.value)
    assert "greater than or equal to 1" in str(caught.value)


def test_environment_source(monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
    monkeypatch.setenv("FLEETLINK_POSTGRES_PASSWORD", sentinel)
    assert Settings().postgres_password.get_secret_value() == sentinel
    source = EnvironmentSecretSource({"FLEETLINK_POSTGRES_PASSWORD": sentinel})
    assert sentinel not in repr(source)
    assert Settings(secret_source=source).postgres_password.get_secret_value() == sentinel
    assert source.resolve("FLEETLINK_ABSENT") is None
    assert (
        Settings(
            secret_source=source, postgres_password=SecretStr("explicit")
        ).postgres_password.get_secret_value()
        == "explicit"
    )


@pytest.mark.parametrize(
    "feature,field",
    [("database_enabled", "postgres_password"), ("celery_enabled", "rabbitmq_password")],
)
def test_required_secret(feature: str, field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(secret_source=None, **{feature: True, field: SecretStr("")})
    with pytest.raises(ValidationError, match="explicit"):
        Settings(secret_source=None, environment="production", **{feature: True})
    assert Settings(secret_source=None, **{feature: True}).environment == "local"


def test_source_has_no_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sentinel: str) -> None:
    (tmp_path / ".env").write_text(f"FLEETLINK_POSTGRES_PASSWORD={sentinel}\n")
    monkeypatch.chdir(tmp_path)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Secret source attempted unexpected I/O")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(socket, "socket", unexpected)
    assert EnvironmentSecretSource({}).resolve("FLEETLINK_POSTGRES_PASSWORD") is None
    assert Settings().postgres_password.get_secret_value() != sentinel


@pytest.mark.parametrize("service", ["postgres", "redis", "rabbitmq"])
def test_safe_connection_target(service: str, sentinel: str) -> None:
    settings = Settings(
        postgres_password=SecretStr(sentinel),
        rabbitmq_password=SecretStr(sentinel),
        redis_password=SecretStr(sentinel),
    )
    target = settings.connection_target(service)  # type: ignore[arg-type]
    assert "127.0.0.1" in target
    assert sentinel not in target and "@" not in target


def test_database_error(sentinel: str) -> None:
    with pytest.raises(DatabaseError) as caught, _safe_errors():
        raise DatabaseOperationalError(sentinel, {}, OSError(sentinel))
    assert sentinel not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("shutdown", [False, True])
def test_redis_error(shutdown: bool, sentinel: str) -> None:
    client = TechnicalRedis(Settings(redis_password=SecretStr(sentinel)))
    method = "aclose" if shutdown else "ping"
    setattr(client._client, method, AsyncMock(side_effect=RedisConnectionError(sentinel)))
    with pytest.raises(RedisError) as caught:
        asyncio.run(client.close() if shutdown else client.ping())
    assert sentinel not in str(caught.value)
    assert caught.value.__suppress_context__
    if not shutdown:
        asyncio.run(client.close())


def test_broker_failure(sentinel: str) -> None:
    app = create_celery(Settings(celery_enabled=True, rabbitmq_password=SecretStr(sentinel)))
    try:
        with pytest.raises(BrokerError) as caught, safe_broker_errors(app):
            raise OperationalError(sentinel)
        assert sentinel not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        app.close()


@pytest.mark.parametrize("formatter", [JsonFormatter, WorkerFormatter])
def test_structured_logs(formatter: type[JsonFormatter], sentinel: str) -> None:
    for message, args in (
        (sentinel, ()),
        ("request_failed", (sentinel,)),
        (Settings(postgres_password=SecretStr(sentinel)), ()),
    ):
        record = logging.LogRecord("fleetlink.worker", logging.ERROR, "", 0, message, args, None)
        record.error_type = sentinel
        record.duration_ms = sentinel
        record.secret = sentinel
        output = formatter().format(record)
        assert sentinel not in output
        assert json.loads(output)["event"] == "application_event"


def test_http_problem_and_headers(sentinel: str) -> None:
    app = create_app()

    @app.get("/failure")
    def fail() -> None:
        raise HTTPException(
            503, detail=sentinel, headers={"X-Internal": sentinel, "Retry-After": sentinel}
        )

    @app.get("/unexpected")
    def unexpected() -> None:
        raise OSError(sentinel)

    with TestClient(app) as client:
        for path in ("/failure", "/unexpected"):
            response = client.get(path)
            assert len(response.json()) == 7
            assert sentinel not in response.text
            assert sentinel not in str(response.headers)


def test_telemetry_failure_events_and_metrics(sentinel: str) -> None:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    runtime = Telemetry(
        Settings(postgres_password=SecretStr(sentinel)),
        "fleetlink-api",
        span_exporter=exporter,
        metric_reader=reader,
    )
    try:
        with pytest.raises(OSError), runtime.task({}, "fleetlink.technical.probe.v1"):
            raise OSError(sentinel)
        spans = exporter.get_finished_spans()
        assert spans
        for span in spans:
            assert sentinel not in str(
                (span.name, span.attributes, span.events, span.resource.attributes)
            )
            assert not span.events
        metrics = reader.get_metrics_data()
        assert metrics is not None
        assert sentinel not in str(metrics)
    finally:
        runtime.shutdown()


def test_task_diagnostics(sentinel: str) -> None:
    with pytest.raises(InvalidProbe) as caught:
        validate_payload({"probe_id": sentinel, "value": sentinel})
    assert sentinel not in str(caught.value)


def test_concurrent_isolation() -> None:
    def operation(index: int) -> tuple[str, str]:
        secret = f"FAKE-FL008-CONCURRENT-{index}-{uuid4()}"
        settings = Settings(
            secret_source=EnvironmentSecretSource({"FLEETLINK_POSTGRES_PASSWORD": secret})
        )
        assert settings.postgres_password.get_secret_value() == secret
        record = logging.LogRecord("fleetlink", logging.INFO, "", 0, secret, (), None)
        return secret, JsonFormatter().format(record) + repr(settings)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(operation, range(12)))
    for secret, _ in results:
        assert all(secret not in output for _, output in results)


def test_source_snapshot_rotation(monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
    monkeypatch.setenv("FLEETLINK_POSTGRES_PASSWORD", sentinel)
    source = EnvironmentSecretSource()
    settings = Settings(secret_source=source)
    monkeypatch.setenv("FLEETLINK_POSTGRES_PASSWORD", "FAKE-ROTATED-DO-NOT-USE")
    assert source.resolve("FLEETLINK_POSTGRES_PASSWORD") == SecretStr(sentinel)
    assert settings.postgres_password == SecretStr(sentinel)
    assert Settings().postgres_password != settings.postgres_password


@pytest.mark.parametrize("encoded", [False, True])
def test_redactor_encoding_and_context(encoded: bool, sentinel: str) -> None:
    from urllib.parse import quote

    from fleetlink.core.logging import correlation_id
    from fleetlink.core.secrets import active_redactor, redaction_scope

    secret = sentinel + ":@/%"
    settings = Settings(postgres_password=SecretStr(secret))
    value = quote(secret, safe="") if encoded else secret
    previous = active_redactor.get()
    with redaction_scope(settings.redactor()):
        token = correlation_id.set(value)
        try:
            record = logging.LogRecord(
                "fleetlink", logging.INFO, "", 0, "request_completed", (), None
            )
            output = JsonFormatter().format(record)
            assert value not in output
            assert json.loads(output)["correlation_id"] == "<redacted>"
        finally:
            correlation_id.reset(token)
    assert active_redactor.get() is previous


def test_secret_correlation_replaced(sentinel: str) -> None:
    app = create_app(Settings(postgres_password=SecretStr(sentinel)))
    with TestClient(app) as client:
        response = client.get("/missing", headers={"X-Correlation-ID": sentinel})
    assert sentinel not in response.text
    assert sentinel not in str(response.headers)


def test_telemetry_known_secret_names_and_resource(sentinel: str) -> None:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    runtime = Telemetry(
        Settings(postgres_password=SecretStr(sentinel)),
        sentinel,
        span_exporter=exporter,
        metric_reader=reader,
    )
    try:
        with runtime.operation(sentinel, attributes={"db.system.name": sentinel}):
            pass
        with runtime.task({}, sentinel):
            pass
        for span in exporter.get_finished_spans():
            assert sentinel not in str(
                (span.name, span.attributes, span.resource.attributes, span.events)
            )
        assert sentinel not in str(reader.get_metrics_data())
    finally:
        runtime.shutdown()


def test_async_redaction_scope_isolation() -> None:
    from fleetlink.core.logging import correlation_id
    from fleetlink.core.secrets import active_redactor, redaction_scope

    async def run(secret: str) -> str:
        settings = Settings(postgres_password=SecretStr(secret))
        with redaction_scope(settings.redactor()):
            token = correlation_id.set(secret)
            try:
                await asyncio.sleep(0)
                assert active_redactor.get().text(secret) == "<redacted>"
                record = logging.LogRecord(
                    "fleetlink", logging.INFO, "", 0, "request_completed", (), None
                )
                return JsonFormatter().format(record)
            finally:
                correlation_id.reset(token)

    async def concurrent() -> None:
        secrets = [f"FAKE-FL008-ASYNC-{uuid4()}" for _ in range(8)]
        outputs = await asyncio.gather(*(run(secret) for secret in secrets))
        for secret in secrets:
            assert all(secret not in output for output in outputs)

    asyncio.run(concurrent())


def test_frozen_assignment_error(sentinel: str) -> None:
    settings = Settings()
    with pytest.raises(ValidationError) as caught:
        settings.postgres_password = SecretStr(sentinel)
    assert sentinel not in str(caught.value.errors())
    assert sentinel not in caught.value.json()
    assert "frozen" in str(caught.value)


def test_injected_source_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch, sentinel: str
) -> None:
    monkeypatch.setenv("FLEETLINK_POSTGRES_PASSWORD", sentinel)
    source = EnvironmentSecretSource({})
    assert Settings(secret_source=source).postgres_password.get_secret_value() != sentinel
    assert sentinel not in repr(source._secrets)


def test_broker_connection_entry_error(monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
    app = create_celery(Settings(celery_enabled=True))
    try:

        def fail() -> None:
            raise OSError(sentinel)

        monkeypatch.setattr(app, "connection_for_write", fail)
        with pytest.raises(BrokerError) as caught, safe_broker_errors(app):
            pytest.fail("Failed connection construction must not enter the body")
        assert sentinel not in str(caught.value)
    finally:
        app.close()


def test_worker_shutdown_error(monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
    from unittest.mock import Mock

    from fleetlink import worker
    from fleetlink.core.secrets import active_redactor

    settings = Settings(celery_enabled=True, rabbitmq_password=SecretStr(sentinel))
    app = Mock()
    app.Worker.return_value.exitcode = 0
    app.close.side_effect = OSError(sentinel)
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "create_celery", lambda *args, **kwargs: app)
    previous = active_redactor.get()
    with pytest.raises(BrokerError) as caught:
        worker.main()
    assert sentinel not in str(caught.value)
    assert active_redactor.get() is previous


def test_broker_connection_exit_error(monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
    from unittest.mock import MagicMock

    class TransportFailure(Exception):
        pass

    app = create_celery(Settings(celery_enabled=True))
    connection = MagicMock()
    connection.__enter__.return_value.connection_errors = (TransportFailure,)
    connection.__enter__.return_value.channel_errors = ()
    connection.__exit__.side_effect = TransportFailure(sentinel)
    try:
        monkeypatch.setattr(app, "connection_for_write", lambda: connection)
        with pytest.raises(BrokerError) as caught, safe_broker_errors(app):
            pass
        assert sentinel not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        app.close()
