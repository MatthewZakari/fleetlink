"""Opt-in real Redis/RabbitMQ/prefork worker tests; missing services are failures."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from confirm_proxy import ConfirmProxy
from kombu import Exchange, Queue
from pydantic import SecretStr

from fleetlink.core.config import Settings
from fleetlink.infrastructure.broker import (
    BrokerError,
    CompletionTimeout,
    TechnicalProducer,
    safe_broker_errors,
)
from fleetlink.infrastructure.redis import RedisError, TechnicalRedis
from fleetlink.infrastructure.tasks import TASK_NAME, InvalidProbe, ProbeExhausted, ProbePayload


@pytest.fixture
def settings() -> Settings:
    if os.environ.get("FLEETLINK_BROKER_TESTS") != "1":
        pytest.fail("Explicit integration opt-in required: FLEETLINK_BROKER_TESTS=1")
    return Settings(
        environment="test",
        celery_enabled=True,
        redis_enabled=True,
        celery_queue=f"fleetlink.technical.v1.fl006.{uuid4().hex}",
        celery_concurrency=1,
    )


def test_redis_connectivity_ttl_cleanup_and_refusal(settings: Settings) -> None:
    async def run() -> None:
        adapter = TechnicalRedis(settings)
        key, sentinel = uuid4(), uuid4()
        try:
            assert await adapter.ping()
            await adapter.write(sentinel, "unrelated-run-resource", ttl_seconds=30)
            await adapter.write(key, "fl006-probe", ttl_seconds=1)
            assert await adapter.read(key) == "fl006-probe"
            async with asyncio.timeout(5):
                while await adapter.read(key) is not None:
                    # Poll the actual TTL state with a deadline; elapsed sleep is not evidence.
                    await asyncio.sleep(0.02)
            await adapter.write(key, "cleanup", ttl_seconds=30)
            await adapter.delete(key)
            assert await adapter.read(key) is None
            assert await adapter.read(sentinel) == "unrelated-run-resource"
        finally:
            try:
                await adapter.delete(key)
                await adapter.delete(sentinel)
            finally:
                await adapter.close()
        assert not adapter._client.connection_pool._in_use_connections
        assert all(
            not connection.is_connected
            for connection in adapter._client.connection_pool._available_connections
        )
        with pytest.raises(RuntimeError, match="closed"):
            await adapter.ping()

        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            failed = TechnicalRedis(
                settings.model_copy(
                    update={
                        "redis_host": "127.0.0.1",
                        "redis_port": reserved.getsockname()[1],
                    }
                )
            )
            try:
                started = time.monotonic()
                with pytest.raises(RedisError):
                    await failed.ping()
                assert time.monotonic() - started < 5
            finally:
                await failed.close()

        wrong_auth = TechnicalRedis(
            settings.model_copy(
                update={
                    "redis_password": SecretStr("synthetic-invalid-fl006-password"),
                    "redis_username": SecretStr(f"fl006-absent-{uuid4().hex}"),
                }
            )
        )
        try:
            with pytest.raises(RedisError) as caught:
                await wrong_auth.ping()
            assert "synthetic-invalid" not in str(caught.value)
        finally:
            await wrong_auth.close()

    asyncio.run(run())


@contextmanager
def worker(settings: Settings, log: Path) -> Iterator[subprocess.Popen[bytes]]:
    environment = os.environ.copy()
    environment.update(
        {
            "FLEETLINK_CELERY_ENABLED": "true",
            "FLEETLINK_CELERY_QUEUE": settings.celery_queue,
            "FLEETLINK_CELERY_CONCURRENCY": "1",
        }
    )
    with log.open("wb") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "fleetlink.worker"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()  # Celery TERM: warm shutdown, no queue purge.
            try:
                code = process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail("Worker did not complete graceful shutdown within 20 seconds")
            assert code == 0, f"Worker failed (exit {code}); sanitized log: {log}"


def test_real_worker_completion_retries_restart_and_queue_isolation(
    settings: Settings,
    tmp_path: Path,
) -> None:
    producer = TechnicalProducer(settings)
    sentinel = f"fleetlink.technical.v1.fl006.{uuid4().hex}"
    probe = ProbePayload(probe_id=str(uuid4()), value=21)
    try:
        with safe_broker_errors(producer.app), producer.app.connection_for_write() as connection:
            Queue(sentinel, durable=True)(connection).declare()
        # A confirmed publish succeeds without any consumer; completion must time out.
        pending = producer.publish(probe)
        with pytest.raises(CompletionTimeout):
            producer.wait(pending, timeout=0.5)

        first_log = tmp_path / "worker-first.log"
        with worker(settings, first_log) as process:
            assert producer.wait(pending) == {
                "probe_id": probe.probe_id,
                "value": 42,
                "attempts": 1,
            }
            assert process.poll() is None
            retried = producer.publish(probe.model_copy(update={"failures_before_success": 2}))
            assert producer.wait(retried)["attempts"] == 3
            exhausted = producer.publish(probe.model_copy(update={"failures_before_success": 3}))
            with pytest.raises(ProbeExhausted, match="attempts=3"):
                producer.wait(exhausted)
            rejected = producer.publish(probe.model_copy(update={"reject": True}))
            with pytest.raises(InvalidProbe, match="attempts=1"):
                producer.wait(rejected)
            malformed = producer.app.send_task(
                TASK_NAME,
                args=[{"unexpected": "synthetic-untrusted-payload"}],
                reply_to=producer.backend.oid,
                timeout=settings.broker_operation_timeout,
                confirm_timeout=settings.broker_operation_timeout,
            )
            with pytest.raises(InvalidProbe, match="Invalid technical probe payload"):
                producer.wait(malformed)
        # The prior process shut down cleanly. Queued work survives its absence/restart.
        recovered = producer.publish(probe)
        with pytest.raises(CompletionTimeout):
            producer.wait(recovered, timeout=0.5)
        with worker(settings, tmp_path / "worker-restarted.log"):
            assert producer.wait(recovered)["value"] == 42
        log_text = first_log.read_text()
        assert "technical_task_retry" in log_text
        assert "ProbeExhausted" in log_text
        assert "InvalidProbe" in log_text
        assert "amqp://" not in log_text
        assert settings.rabbitmq_password.get_secret_value() not in log_text
        assert "failures_before_success" not in log_text
        assert "synthetic-untrusted-payload" not in log_text
        assert log_text.count('"event": "technical_task_retry"') == 4
    finally:
        producer.close()
        # Delete only this run's exact queue and exchange, never purge shared resources.
        with safe_broker_errors(producer.app), producer.app.connection_for_write() as connection:
            Queue(settings.celery_queue)(connection).delete(if_unused=True, if_empty=True)
            Exchange(settings.celery_queue)(connection).delete(if_unused=True)
            Queue(sentinel)(connection).queue_declare(passive=True)
            Queue(sentinel)(connection).delete(if_unused=True, if_empty=True)
            Queue(producer.backend.oid)(connection).delete(if_unused=True)


def test_rabbitmq_refusal_and_auth_failure(settings: Settings) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        failed = settings.model_copy(
            update={
                "rabbitmq_host": "127.0.0.1",
                "rabbitmq_amqp_port": reserved.getsockname()[1],
            }
        )
        with TechnicalProducer(failed) as producer:
            started = time.monotonic()
            with pytest.raises(BrokerError):
                producer.publish(ProbePayload(probe_id=str(uuid4()), value=0))
            assert time.monotonic() - started < 5
    failed_auth = settings.model_copy(
        update={
            "rabbitmq_password": SecretStr("synthetic-invalid-fl006-password"),
        }
    )
    with TechnicalProducer(failed_auth) as producer:
        with pytest.raises(BrokerError) as caught:
            producer.publish(ProbePayload(probe_id=str(uuid4()), value=0))
        assert "synthetic-invalid" not in str(caught.value)


def test_stalled_amqp_handshake_is_bounded(settings: Settings) -> None:
    # Real TCP peer accepts but never answers AMQP. Unlike refusal this exercises timeout.
    stopped = threading.Event()
    accepted = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def stall() -> None:
            with listener.accept()[0]:
                accepted.set()
                stopped.wait(5)

        thread = threading.Thread(target=stall, daemon=True)
        thread.start()
        failed = settings.model_copy(
            update={
                "rabbitmq_host": "127.0.0.1",
                "rabbitmq_amqp_port": listener.getsockname()[1],
                "broker_connect_timeout": 0.25,
                "broker_operation_timeout": 0.25,
            }
        )
        try:
            with TechnicalProducer(failed) as producer:
                started = time.monotonic()
                with pytest.raises(BrokerError):
                    producer.publish(ProbePayload(probe_id=str(uuid4()), value=0))
                assert accepted.is_set()
                assert time.monotonic() - started < 3
        finally:
            stopped.set()
            thread.join(timeout=6)
            assert not thread.is_alive()


def test_publish_confirmation_timeout_has_uncertain_outcome(settings: Settings) -> None:
    proxy = ConfirmProxy(settings.rabbitmq_host, settings.rabbitmq_amqp_port)
    proxy.start()
    proxied = settings.model_copy(
        update={
            "rabbitmq_host": "127.0.0.1",
            "rabbitmq_amqp_port": proxy.port,
            "broker_operation_timeout": 0.25,
        }
    )
    producer = TechnicalProducer(proxied)
    try:
        started = time.monotonic()
        with pytest.raises(BrokerError, match="uncertain"):
            producer.publish(ProbePayload(probe_id=str(uuid4()), value=1))
        assert proxy.published.is_set(), "Publish frame never reached the real RabbitMQ broker"
        assert time.monotonic() - started < 3
    finally:
        proxy.close()
        producer.close()
        # This run owns the uncertain message; no worker is attached to its unique queue.
        with TechnicalProducer(settings) as cleanup:
            with safe_broker_errors(cleanup.app), cleanup.app.connection_for_write() as connection:
                queue = Queue(settings.celery_queue)(connection)
                # Kombu returns AMQP's (name, message_count, consumer_count); stubs lag.
                declared = cast(tuple[str, int, int], queue.queue_declare(passive=True))
                assert declared[1:] == (1, 0)
                queue.delete(if_unused=True)
                Exchange(settings.celery_queue)(connection).delete(if_unused=True)
                Queue(producer.backend.oid)(connection).delete(if_unused=True)


def test_real_worker_w3c_trace_propagation(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from fleetlink.observability import Telemetry

    exporter = InMemorySpanExporter()
    telemetry = Telemetry(settings, "fleetlink-api", span_exporter=exporter)
    probe = ProbePayload(probe_id=str(uuid4()), value=21, failures_before_success=1)
    # Explicit worker export to a refused loopback port proves collector failure isolation.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        monkeypatch.setenv("FLEETLINK_TELEMETRY_ENABLED", "true")
        monkeypatch.setenv(
            "FLEETLINK_OTEL_EXPORTER_OTLP_ENDPOINT",
            f"http://127.0.0.1:{reserved.getsockname()[1]}",
        )
        monkeypatch.setenv("FLEETLINK_OTEL_EXPORT_TIMEOUT_SECONDS", "0.2")
        producer = TechnicalProducer(settings, telemetry=telemetry)
        log = tmp_path / "worker-tracing.log"
        try:
            with worker(settings, log):
                result = producer.publish(probe)
                assert producer.wait(result)["attempts"] == 2
            span = exporter.get_finished_spans()[0]
            assert span.context is not None
            expected = format(span.context.trace_id, "032x")
            events = []
            for line in log.read_text().splitlines():
                if line.startswith("{"):
                    entry = json.loads(line)
                    if entry.get("event") == "technical_task_started":
                        events.append(entry)
            assert len(events) == 2
            for entry in events:
                assert entry["trace_id"] == expected
                assert entry["correlation_id"] == probe.probe_id
                assert entry["service"] == "fleetlink-worker"
                assert len(entry["span_id"]) == 16
            assert events[0]["span_id"] != events[1]["span_id"]
            assert "telemetry_export_failed" in log.read_text()
        finally:
            producer.close()
            telemetry.shutdown()
            with (
                safe_broker_errors(producer.app),
                producer.app.connection_for_write() as connection,
            ):
                Queue(settings.celery_queue)(connection).delete(if_unused=True, if_empty=True)
                Exchange(settings.celery_queue)(connection).delete(if_unused=True)
                Queue(producer.backend.oid)(connection).delete(if_unused=True)
