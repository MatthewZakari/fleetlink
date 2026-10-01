"""RabbitMQ-only Celery composition and synchronous technical producer."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol, cast
from uuid import uuid4

from celery import Celery
from celery.backends.rpc import RPCBackend
from celery.result import AsyncResult
from kombu import Exchange, Producer, Queue
from kombu.connection import ConnectionPool
from kombu.exceptions import OperationalError
from kombu.utils.objects import cached_property
from opentelemetry import trace

from fleetlink.core.config import Settings
from fleetlink.infrastructure.tasks import TASK_NAME, ProbePayload, register_technical_task
from fleetlink.observability import Telemetry, TelemetrySlot
from fleetlink.observability.runtime import inject


class BrokerError(RuntimeError):
    """Transport failed; publication/execution outcome may be uncertain."""


class CompletionTimeout(TimeoutError):
    """No completion observed; this does not cancel an accepted task."""


class _PoolOwner(Protocol):
    # Celery's default pool cache points into Kombu's process-global pool registry.
    # A dedicated producer owns this cache instead; never close another app's pool.
    _pool: ConnectionPool | None


class TechnicalRPCBackend(RPCBackend):
    """One short-lived, bounded reply queue per producer thread, never a ledger."""

    retry_policy = {"max_retries": 0, "interval_start": 0, "interval_step": 0, "interval_max": 0}
    # These runtime attributes are absent from celery-types' RPCBackend declarations.
    app: Celery
    exchange: Exchange

    @cached_property
    def oid(self) -> str:
        return f"fleetlink.technical.fl006.reply.{self.app.thread_oid}"

    @property
    def binding(self) -> Queue:
        return self.Queue(
            self.oid,
            self.exchange,
            self.oid,
            # RabbitMQ 4.3 rejects non-durable, non-exclusive queues. Result messages
            # remain transient; this bounded queue auto-deletes/expires independently.
            durable=True,
            auto_delete=True,
            expires=60,
            message_ttl=60,
            max_length=32,
            max_length_bytes=65536,
        )

    def on_task_call(self, producer: Producer, task_id: str) -> None:
        # Upstream's maybe_declare(retry=True) has an unbounded default policy.
        # A worker retry retains the original reply_to and needs no new reply queue.
        if self.app.current_worker_task is None:
            channel = producer.channel
            if channel is None:
                raise RuntimeError("Technical producer requires an attached channel")
            self.binding(channel).declare()


def create_celery(
    settings: Settings | None = None, *, telemetry: TelemetrySlot | None = None
) -> Celery:
    settings = settings if settings is not None else Settings()
    if not settings.celery_enabled:
        raise RuntimeError("Celery is disabled; explicitly set FLEETLINK_CELERY_ENABLED=true")
    if any(
        key in os.environ
        for key in (
            "CELERY_BROKER_URL",
            "CELERY_BROKER_READ_URL",
            "CELERY_BROKER_WRITE_URL",
            "CELERY_RESULT_BACKEND",
            "CELERY_CONFIG_MODULE",
            "CELERY_LOADER",
        )
    ):
        raise RuntimeError("Unsupported Celery environment override; use FLEETLINK_ settings")
    queue = settings.celery_queue
    app = Celery(
        "fleetlink.technical",
        broker=settings.broker_url().get_secret_value(),
        backend="fleetlink.infrastructure.broker:TechnicalRPCBackend",
        set_as_current=False,
        fixups=[],
    )
    app.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        result_accept_content=["json"],
        enable_utc=True,
        timezone="UTC",
        task_queues=(
            Queue(
                queue,
                Exchange(queue, type="direct", durable=True),
                queue,
                durable=True,
                queue_arguments={
                    "x-message-ttl": 60000,
                    "x-max-length": 100,
                    "x-max-length-bytes": 262144,
                    "x-overflow": "reject-publish",
                },
            ),
        ),
        task_default_queue=queue,
        task_default_exchange=queue,
        task_default_routing_key=queue,
        task_routes={TASK_NAME: {"queue": queue, "routing_key": queue}},
        task_create_missing_queues=False,
        task_default_delivery_mode="persistent",
        task_publish_retry=False,
        task_publish_retry_policy={"max_retries": 0},
        broker_pool_limit=2,
        broker_connection_timeout=settings.broker_connect_timeout,
        broker_transport_options={
            "confirm_publish": True,
            "read_timeout": settings.broker_operation_timeout,
            "write_timeout": settings.broker_operation_timeout,
            "max_retries": 0,
        },
        broker_connection_retry_on_startup=True,
        broker_connection_retry=True,
        broker_connection_max_retries=3,
        broker_channel_error_retry=False,
        broker_heartbeat=10,
        result_expires=60,
        result_persistent=False,
        result_cache_max=32,
        result_backend_always_retry=False,
        task_acks_late=True,
        task_acks_on_failure_or_timeout=True,
        task_reject_on_worker_lost=False,
        task_soft_time_limit=5,
        task_time_limit=10,
        worker_prefetch_multiplier=1,
        worker_concurrency=settings.celery_concurrency,
        worker_max_tasks_per_child=100,
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        worker_soft_shutdown_timeout=10,
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
    )
    register_technical_task(app, telemetry)
    # Finalize once, then allowlist the sole supported task; no canvas/cleanup tasks.
    for name in tuple(app.tasks):
        if name != TASK_NAME:
            app.tasks.unregister(name)
    return app


@contextmanager
def safe_broker_errors(app: Celery) -> Iterator[None]:
    # Use the selected transport's concrete exception families. Programming errors propagate.
    transport_errors: tuple[type[BaseException], ...] = (OperationalError, OSError)
    try:
        with app.connection_for_write() as connection:
            transport_errors += connection.connection_errors + connection.channel_errors
            yield
    except transport_errors:
        raise BrokerError("RabbitMQ operation failed; outcome may be uncertain") from None


class TechnicalProducer:
    """Single-thread owner. Use in a dedicated process, never on an ASGI event loop."""

    def __init__(self, settings: Settings, *, telemetry: Telemetry | None = None) -> None:
        self._settings = settings
        self.telemetry = telemetry
        self._owns_telemetry = telemetry is None
        self.app = create_celery(settings)
        self._connections = ConnectionPool(self.app.connection_for_write(), limit=2)
        cast(_PoolOwner, self.app)._pool = self._connections
        self._timeout = settings.broker_operation_timeout
        self._closed = False
        self._published = False

    @property
    def backend(self) -> TechnicalRPCBackend:
        return cast(TechnicalRPCBackend, self.app.backend)

    def publish(self, payload: ProbePayload) -> AsyncResult[dict[str, object]]:
        if self._closed:
            raise RuntimeError("Producer is closed")
        if self.telemetry is None:
            self.telemetry = Telemetry(self._settings, "fleetlink-api")
        with (
            self.telemetry.operation(
                "celery.publish",
                kind=trace.SpanKind.PRODUCER,
                attributes={"messaging.system": "rabbitmq", "celery.task.name": TASK_NAME},
            ),
            safe_broker_errors(self.app),
            self.app.connection_for_write() as connection,
        ):
            with connection.Producer() as producer:
                result: AsyncResult[dict[str, object]] = self.app.send_task(
                    TASK_NAME,
                    args=[payload.model_dump(mode="json")],
                    headers=inject(),
                    task_id=str(uuid4()),
                    producer=producer,
                    retry=False,
                    timeout=self._timeout,
                    confirm_timeout=self._timeout,
                    reply_to=self.backend.oid,
                    argsrepr="<technical payload omitted>",
                    kwargsrepr="{}",
                    expires=60,
                )
                self._published = True
                return result

    def wait(
        self, result: AsyncResult[dict[str, object]], *, timeout: float = 20
    ) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("Producer is closed")
        if not 0 < timeout <= 60:
            raise ValueError("Completion deadline must be greater than 0 and at most 60 seconds")
        deadline = time.monotonic() + timeout
        with safe_broker_errors(self.app):
            while time.monotonic() < deadline:
                # ready() polls RPC metadata and caches a terminal result. get() then
                # returns/raises from that cache without starting an async consumer.
                # Celery 5.6.3's RPC consumer otherwise reconnects on normal timeouts.
                if result.ready():
                    return result.get(timeout=timeout)
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise CompletionTimeout("No task completion observed before deadline")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._published:
                with safe_broker_errors(self.app), self.app.connection_for_write() as connection:
                    self.backend.binding(connection).delete(if_unused=True)
        finally:
            try:
                with safe_broker_errors(self.app):
                    self._connections.force_close_all()
            finally:
                try:
                    with safe_broker_errors(self.app):
                        self.app.close()
                finally:
                    if self._owns_telemetry and self.telemetry is not None:
                        self.telemetry.shutdown()

    def __enter__(self) -> TechnicalProducer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
