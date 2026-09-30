"""Infrastructure-free Redis/Celery contracts. No eager result is integration evidence."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from kombu import Connection
from pydantic import SecretStr, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from fleetlink.core.config import Settings
from fleetlink.core.dependencies import get_redis
from fleetlink.infrastructure.broker import (
    BrokerError,
    TechnicalProducer,
    create_celery,
    safe_broker_errors,
)
from fleetlink.infrastructure.redis import RedisError, TechnicalRedis
from fleetlink.infrastructure.tasks import (
    TASK_NAME,
    InvalidProbe,
    ProbeExhausted,
    ProbePayload,
    TransientProbeError,
    execute_probe,
    validate_payload,
)
from fleetlink.main import create_app
from fleetlink.worker import WorkerFormatter


def test_broker_url_reserved_credentials() -> None:
    secret = "synthetic:@/%?#"
    settings = Settings(
        rabbitmq_user=SecretStr(secret),
        rabbitmq_password=SecretStr(secret),
        rabbitmq_vhost="technical /:@",
        redis_password=SecretStr(secret),
    )
    with Connection(settings.broker_url().get_secret_value()) as connection:
        assert connection.userid == secret
        assert connection.password == secret
        assert connection.virtual_host == "technical /:@"
    assert secret not in repr(settings)
    assert secret not in repr(settings.broker_url())


@pytest.mark.parametrize(
    "key",
    [
        "CELERY_BROKER_URL",
        "CELERY_BROKER_READ_URL",
        "CELERY_BROKER_WRITE_URL",
        "CELERY_RESULT_BACKEND",
        "CELERY_CONFIG_MODULE",
        "CELERY_LOADER",
    ],
)
def test_celery_cannot_bypass_settings(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(key, "redis://synthetic-secret")
    with pytest.raises(RuntimeError, match="use FLEETLINK_ settings") as caught:
        create_celery(Settings(celery_enabled=True))
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("REDIS_PORT", "0"),
        ("REDIS_MAX_CONNECTIONS", "51"),
        ("REDIS_CONNECT_TIMEOUT", "nan"),
        ("REDIS_OPERATION_TIMEOUT", "0"),
        ("BROKER_CONNECT_TIMEOUT", "inf"),
        ("BROKER_OPERATION_TIMEOUT", "31"),
        ("RABBITMQ_AMQP_PORT", "65536"),
        ("CELERY_CONCURRENCY", "0"),
        ("CELERY_QUEUE", "unrelated"),
        ("RABBITMQ_HOST", "host/path?secret"),
    ],
)
def test_invalid_settings(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(f"FLEETLINK_{key}", value)
    with pytest.raises(ValidationError):
        Settings()


def test_disabled_resources_and_no_network_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock(side_effect=AssertionError("must not create Redis"))
    monkeypatch.setattr("fleetlink.main.TechnicalRedis", factory)
    app = create_app(Settings(redis_enabled=False))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        with pytest.raises(RuntimeError, match="disabled or outside"):
            get_redis(Request({"type": "http", "app": app}))
    create_app(Settings(redis_enabled=True))
    factory.assert_not_called()
    with pytest.raises(RuntimeError, match="disabled"):
        create_celery(Settings(celery_enabled=False))


def test_independent_resources_lifespan_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    close = AsyncMock()
    monkeypatch.setattr(TechnicalRedis, "close", close)
    first = create_app(Settings(redis_enabled=True))
    second = create_app(Settings(redis_enabled=True))
    assert first.state.redis is None
    with TestClient(first) as client, TestClient(second):
        assert first.state.redis is not second.state.redis
        assert get_redis(Request({"type": "http", "app": first})) is first.state.redis
        assert client.get("/ready").json()["dependency_checks"] == "not_configured"
    assert first.state.redis is None
    assert second.state.redis is None
    assert close.await_count == 2


def test_partial_startup_releases_database(monkeypatch: pytest.MonkeyPatch) -> None:
    database = Mock(dispose=AsyncMock())
    monkeypatch.setattr("fleetlink.main.Database", lambda settings: database)
    monkeypatch.setattr("fleetlink.main.TechnicalRedis", Mock(side_effect=ValueError("fixture")))
    app = create_app(Settings(redis_enabled=True, database_enabled=True))
    with pytest.raises(ValueError, match="fixture"), TestClient(app):
        pytest.fail("startup must fail")
    database.dispose.assert_awaited_once()
    assert app.state.database is None
    assert not app.state.readiness.initialized


def test_redis_errors_cancellation_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        client = AsyncMock()
        monkeypatch.setattr("fleetlink.infrastructure.redis.Redis", lambda **kwargs: client)
        adapter = TechnicalRedis(Settings())
        for error in (RedisConnectionError("synthetic-secret"), TimeoutError("synthetic-secret")):
            client.ping.side_effect = error
            with pytest.raises(RedisError) as caught:
                await adapter.ping()
            assert "synthetic-secret" not in str(caught.value)
            assert caught.value.__suppress_context__
        for unhandled in (
            ValueError("programming"),
            ResponseError("command"),
            asyncio.CancelledError(),
        ):
            client.ping.side_effect = unhandled
            with pytest.raises(type(unhandled)):
                await adapter.ping()
        await adapter.close()
        client.aclose.assert_awaited_once()
        with pytest.raises(RuntimeError, match="closed"):
            await adapter.ping()

    asyncio.run(run())


def test_redis_ttl_and_payload_bounds() -> None:
    async def run() -> None:
        adapter = TechnicalRedis(Settings())
        try:
            for ttl in (0, 301, True):
                with pytest.raises(ValueError, match="TTL"):
                    await adapter.write(uuid4(), "probe", ttl_seconds=ttl)
            with pytest.raises(ValueError, match="256 bytes"):
                await adapter.write(uuid4(), "x" * 257)
        finally:
            await adapter.close()

    asyncio.run(run())


def test_isolated_task_registration_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Connection, "connect", Mock(side_effect=AssertionError("network")))
    with (
        create_celery(Settings(celery_enabled=True)) as first,
        create_celery(
            Settings(
                celery_enabled=True, celery_queue=f"fleetlink.technical.v1.fl006.{uuid4().hex}"
            )
        ) as second,
    ):
        assert first.tasks[TASK_NAME] is not second.tasks[TASK_NAME]
        assert set(first.tasks) == set(second.tasks) == {TASK_NAME}
        assert first.tasks[TASK_NAME].app is first
        assert second.tasks[TASK_NAME].app is second
        assert first.conf.task_default_queue != second.conf.task_default_queue
        assert first.conf.accept_content == ["json"]
        assert first.conf.task_serializer == first.conf.result_serializer == "json"
        assert first.conf.worker_prefetch_multiplier == 1
        assert first.conf.broker_transport_options["confirm_publish"]
        assert not first.conf.task_publish_retry
        assert first.tasks[TASK_NAME].max_retries == 2
        assert not first.conf.task_reject_on_worker_lost
        assert not first.conf.worker_enable_remote_control


def test_payload_validation_and_bounded_retry_policy() -> None:
    probe = ProbePayload(probe_id=str(uuid4()), value=21, failures_before_success=2)
    for attempt in (0, 1):
        with pytest.raises(TransientProbeError):
            execute_probe(probe, attempt)
    assert execute_probe(probe, 2) == {"probe_id": probe.probe_id, "value": 42, "attempts": 3}
    with pytest.raises(ProbeExhausted, match="attempts=3"):
        execute_probe(probe.model_copy(update={"failures_before_success": 3}), 2)
    with pytest.raises(InvalidProbe, match="attempts=1"):
        execute_probe(probe.model_copy(update={"reject": True}), 0)
    for value in ({"secret": "synthetic-secret"}, {**probe.model_dump(), "value": "21"}):
        with pytest.raises(InvalidProbe) as caught:
            validate_payload(value)
        assert "synthetic-secret" not in str(caught.value)


def test_publish_failure_is_safe_and_programming_errors_propagate() -> None:
    with TechnicalProducer(Settings(celery_enabled=True)) as producer:
        with pytest.raises(BrokerError) as caught, safe_broker_errors(producer.app):
            raise OSError("synthetic-secret")
        assert "synthetic-secret" not in str(caught.value)
        with pytest.raises(ValueError, match="programming"), safe_broker_errors(producer.app):
            raise ValueError("programming")
    with pytest.raises(RuntimeError, match="closed"):
        producer.publish(ProbePayload(probe_id=str(uuid4()), value=0))


def test_producer_pools_are_instance_owned() -> None:
    with TechnicalProducer(Settings(celery_enabled=True)) as first:
        with TechnicalProducer(Settings(celery_enabled=True)) as second:
            assert first.app.pool is not second.app.pool
            assert first.app.pool.limit == second.app.pool.limit == 2
        assert id(first.app.pool) == id(first._connections)


def test_worker_logs_omit_library_payloads_and_urls() -> None:
    record = logging.LogRecord(
        "celery.worker",
        logging.ERROR,
        "",
        0,
        "amqp://synthetic-secret payload=%s",
        ("private",),
        None,
    )
    result = WorkerFormatter().format(record)
    assert "synthetic-secret" not in result
    assert "private" not in result
    assert json.loads(result)["service"] == "fleetlink-worker"
    assert json.loads(result)["event"] == "worker_runtime_event"
