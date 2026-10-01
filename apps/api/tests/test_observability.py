"""Deterministic OpenTelemetry contracts; no services or collector required."""

import asyncio
import io
import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from threading import Event
from time import monotonic
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from starlette.types import Message, Receive, Scope, Send

from fleetlink.core.config import Settings
from fleetlink.core.http import Problem, RequestContextMiddleware
from fleetlink.core.logging import JsonFormatter, correlation_id
from fleetlink.infrastructure.broker import TechnicalProducer, create_celery
from fleetlink.infrastructure.redis import TechnicalRedis
from fleetlink.infrastructure.tasks import TASK_NAME, InvalidProbe, ProbePayload
from fleetlink.main import create_app
from fleetlink.observability import Telemetry, TelemetrySlot
from fleetlink.observability.database import DatabaseInstrumentation
from fleetlink.observability.export import DeadlineBatchSpanProcessor, SafeSpanExporter
from fleetlink.observability.http import TelemetryMiddleware
from fleetlink.observability.runtime import extract, inject

TRACE_ID = "1234567890abcdef1234567890abcdef"
PARENT_ID = "1234567890abcdef"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_ID}-01"
SECRET = "synthetic-sensitive-value"


@contextmanager
def memory(
    service: str = "fleetlink-api", *, ratio: float = 1
) -> Iterator[tuple[Telemetry, InMemorySpanExporter, InMemoryMetricReader]]:
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    runtime = Telemetry(
        Settings(environment="test", otel_sample_ratio=ratio),
        service,
        span_exporter=exporter,
        metric_reader=reader,
    )
    try:
        yield runtime, exporter, reader
    finally:
        runtime.shutdown()


def test_default_disabled_and_no_global_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEETLINK_TELEMETRY_ENABLED", raising=False)
    assert not Settings().telemetry_enabled
    global_tracer = trace.get_tracer_provider()
    failure = Mock(side_effect=AssertionError("exporter constructed"))
    monkeypatch.setattr("fleetlink.observability.runtime.OTLPSpanExporter", failure)
    monkeypatch.setattr("fleetlink.observability.runtime.OTLPMetricExporter", failure)
    app = create_app(Settings())
    assert app.state.telemetry is None
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
        current = cast(Telemetry, app.state.telemetry)
        assert current.traces is None
        assert current.metrics is None
    assert app.state.telemetry is None
    assert trace.get_tracer_provider() is global_tracer
    failure.assert_not_called()


@pytest.mark.parametrize("endpoint", ["http://localhost:4318", "https://collector.example/"])
def test_valid_configuration(endpoint: str) -> None:
    settings = Settings(
        telemetry_enabled=True,
        otel_exporter_otlp_endpoint=SecretStr(endpoint),
        otel_sample_ratio=0.5,
        otel_export_timeout_seconds=3,
    )
    assert settings.otel_exporter_otlp_endpoint == SecretStr(endpoint)
    assert endpoint not in repr(settings)
    with pytest.raises(ValidationError):
        settings.otel_sample_ratio = 0


@pytest.mark.parametrize(
    "endpoint",
    [
        f"http://user:{SECRET}@localhost:4318",
        f"https://collector/?token={SECRET}",
        f"https://collector/#{SECRET}",
        f"https://collector/{SECRET}",
        f"ftp://{SECRET}",
        f"http://{SECRET}:99999",
        "http://",
        "http://a b",
        "http://a:0",
        "http://a:bad",
        "http://a:",
        "http://a?",
        "http://a#",
        "http://a..b",
        "http://a\\b",
        "http://%61",
    ],
)
def test_invalid_endpoint_sanitized(endpoint: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(otel_exporter_otlp_endpoint=SecretStr(endpoint))
    assert endpoint not in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("OTEL_SAMPLE_RATIO", "-0.01"),
        ("OTEL_SAMPLE_RATIO", "1.01"),
        ("OTEL_SAMPLE_RATIO", "nan"),
        ("OTEL_SAMPLE_RATIO", "inf"),
        ("OTEL_EXPORT_TIMEOUT_SECONDS", "0"),
        ("OTEL_EXPORT_TIMEOUT_SECONDS", "10.1"),
        ("OTEL_EXPORT_TIMEOUT_SECONDS", "nan"),
        ("OTEL_EXPORT_TIMEOUT_SECONDS", SECRET),
    ],
)
def test_invalid_numeric_settings(monkeypatch: pytest.MonkeyPatch, field: str, value: str) -> None:
    monkeypatch.setenv(f"FLEETLINK_{field}", value)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert SECRET not in str(caught.value)


def test_enabled_requires_endpoint_and_rejects_sdk_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="requires FLEETLINK"):
        Settings(telemetry_enabled=True)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={SECRET}")
    with pytest.raises(ValidationError, match="Unsupported OpenTelemetry") as caught:
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint=SecretStr("http://localhost"))
    assert SECRET not in str(caught.value)


def test_resource_identity_and_log_correlation() -> None:
    with memory("fleetlink-worker") as (runtime, exporter, _):
        token = correlation_id.set("independent-correlation")
        try:
            with runtime.operation("test", parent=extract({"traceparent": TRACEPARENT})) as span:
                payload = json.loads(
                    JsonFormatter().format(
                        logging.LogRecord(
                            "fleetlink.test", logging.INFO, "", 0, "safe_event", (), None
                        )
                    )
                )
                assert payload["trace_id"] == TRACE_ID
                assert payload["span_id"] == format(span.get_span_context().span_id, "016x")
                assert payload["correlation_id"] == "independent-correlation"
        finally:
            correlation_id.reset(token)
        finished = exporter.get_finished_spans()[0]
        assert finished.parent is not None
        assert finished.parent.span_id == int(PARENT_ID, 16)
        assert dict(finished.resource.attributes) == {
            "service.name": "fleetlink-worker",
            "service.version": "0.1.0",
            "deployment.environment.name": "test",
        }
    record = json.loads(
        JsonFormatter().format(
            logging.LogRecord("fleetlink.test", logging.INFO, "", 0, "safe_event", (), None)
        )
    )
    assert "trace_id" not in record and "span_id" not in record


@pytest.mark.parametrize(
    "header", [None, "bad", "00-" + "0" * 32 + "-" + PARENT_ID + "-01", 1, "x" * 257]
)
def test_invalid_propagation_creates_independent_trace(header: object) -> None:
    with memory() as (runtime, exporter, _):
        with runtime.operation("test", parent=extract({"traceparent": header})):
            assert "traceparent" in inject()
        span = exporter.get_finished_spans()[0]
        assert span.context is not None and span.context.is_valid
        assert span.parent is None


def test_http_contract_route_cardinality_and_privacy() -> None:
    with memory() as (runtime, exporter, reader):
        app = create_app(Settings(), telemetry_factory=lambda settings, service: runtime)

        @app.get("/items/{identifier}")
        async def item(identifier: str) -> dict[str, str]:
            return {"value": identifier}

        @app.get("/failure")
        async def failure() -> None:
            raise RuntimeError(SECRET)

        with TestClient(app) as client:
            for identifier in ("dynamic-1", "dynamic-2"):
                response = client.get(
                    f"/items/{identifier}?secret={SECRET}",
                    headers={
                        "traceparent": TRACEPARENT,
                        "x-correlation-id": "kept-id",
                        "authorization": f"Bearer {SECRET}",
                        "cookie": f"secret={SECRET}",
                        "baggage": f"customer={SECRET}",
                        "tracestate": f"vendor={SECRET}",
                    },
                )
                assert response.headers["x-correlation-id"] == "kept-id"
                assert response.headers["x-content-type-options"] == "nosniff"
                assert response.headers["x-frame-options"] == "DENY"
                assert response.headers["referrer-policy"] == "no-referrer"
                assert response.headers["cache-control"] == "no-store"
            failed = client.get("/failure")
            assert Problem.model_validate(failed.json()).code == "internal_error"
            assert failed.status_code == 500
            for path in (f"/absent/{SECRET}", f"/other/{uuid4()}"):
                assert client.get(path).status_code == 404
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/ready").json()["dependency_checks"] == "not_configured"
            data = reader.get_metrics_data()
            assert data is not None
            serialized = data.to_json()
            assert SECRET not in serialized
            assert "dynamic-1" not in serialized and "dynamic-2" not in serialized
            metrics = data.resource_metrics[0].scope_metrics[0].metrics
            assert len(metrics) == 1
            metric = metrics[0]
            assert metric.name == "http.server.request.duration" and metric.unit == "s"
            assert metric.description == "HTTP server request duration"
            assert isinstance(metric.data, Histogram)
            assert sum(point.count for point in metric.data.data_points) == 7
            for point in metric.data.data_points:
                assert set(point.attributes or {}) == {
                    "http.request.method",
                    "http.route",
                    "http.response.status_class",
                }
            spans = exporter.get_finished_spans()
            assert len(spans) == 5
            assert spans[0].name == spans[1].name == "GET /items/{identifier}"
            assert spans[0].context is not None and spans[0].context.trace_id == int(TRACE_ID, 16)
            assert spans[2].status.status_code == trace.StatusCode.ERROR
            assert spans[3].name == spans[4].name == "GET unmatched"
            for span in spans:
                assert SECRET not in repr(span.attributes)
                assert span.events == ()
                assert span.context is not None and not span.context.trace_state
                assert set(span.attributes or {}) == {
                    "http.request.method",
                    "http.route",
                    "http.response.status_code",
                }


def test_http_malformed_and_duplicate_context() -> None:
    with memory() as (runtime, exporter, _):
        app = create_app(Settings(), telemetry_factory=lambda settings, service: runtime)
        with TestClient(app) as client:
            for headers in (
                {"traceparent": "bad"},
                [("traceparent", TRACEPARENT), ("traceparent", TRACEPARENT)],
            ):
                assert client.get("/openapi.json", headers=headers).status_code == 200
        assert all(span.parent is None for span in exporter.get_finished_spans())


def test_concurrent_http_trace_and_correlation_isolation() -> None:
    async def run(runtime: Telemetry) -> None:
        barrier = asyncio.Barrier(2)
        observed: list[dict[str, object]] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await barrier.wait()
            observed.append(
                json.loads(
                    JsonFormatter().format(
                        logging.LogRecord(
                            "fleetlink.test", logging.INFO, "", 0, "safe_event", (), None
                        )
                    )
                )
            )
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def receive() -> Message:
            raise AssertionError("instrumentation must not read a body")

        async def send(message: Message) -> None:
            pass

        outer = TelemetryMiddleware(RequestContextMiddleware(app))
        holder = Mock(state=Mock(telemetry=runtime))
        scopes: list[Scope] = [
            {
                "type": "http",
                "method": "GET",
                "path": "/dynamic/id",
                "app": holder,
                "headers": [
                    (b"x-correlation-id", name.encode()),
                    (b"traceparent", header.encode()),
                ],
            }
            for name, header in (
                ("first", TRACEPARENT),
                ("second", "00-" + "a" * 32 + "-" + PARENT_ID + "-01"),
            )
        ]
        await asyncio.gather(*(outer(scope, receive, send) for scope in scopes))
        assert {(entry["correlation_id"], entry["trace_id"]) for entry in observed} == {
            ("first", TRACE_ID),
            ("second", "a" * 32),
        }
        assert not trace.get_current_span().get_span_context().is_valid
        assert correlation_id.get() is None

    with memory() as (runtime, _, _):
        asyncio.run(run(runtime))


def test_sampling_parent_based() -> None:
    with memory(ratio=0) as (runtime, exporter, _):
        with runtime.operation("root"):
            pass
        with runtime.operation("sampled_parent", parent=extract({"traceparent": TRACEPARENT})):
            pass
        with runtime.operation(
            "unsampled_parent", parent=extract({"traceparent": TRACEPARENT[:-2] + "00"})
        ):
            pass
        assert [span.name for span in exporter.get_finished_spans()] == ["sampled_parent"]


def test_producer_worker_headers_relationship_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    with (
        memory() as (api, producer_spans, _),
        memory("fleetlink-worker") as (worker, worker_spans, _),
    ):
        slot = TelemetrySlot(worker)
        app = create_celery(Settings(celery_enabled=True), telemetry=slot)
        payload = ProbePayload(probe_id=str(uuid4()), value=21)
        with TechnicalProducer(Settings(celery_enabled=True), telemetry=api) as producer:
            connection = MagicMock()
            monkeypatch.setattr(producer.app, "connection_for_write", lambda: connection)
            sender = Mock(return_value=Mock())
            monkeypatch.setattr(producer.app, "send_task", sender)
            with api.operation("upstream"):
                producer.publish(payload)
            captured = sender.call_args.kwargs
        args = captured["args"]
        assert args == [payload.model_dump(mode="json")]
        headers = captured["headers"]
        assert isinstance(headers, dict) and set(headers) == {"traceparent"}
        result = app.tasks[TASK_NAME].apply(args=tuple(args), headers=headers, throw=True)
        assert result.result == {"probe_id": payload.probe_id, "value": 42, "attempts": 1}
        published = producer_spans.get_finished_spans()[0]
        consumed = worker_spans.get_finished_spans()[0]
        assert published.kind == trace.SpanKind.PRODUCER
        assert consumed.kind == trace.SpanKind.CONSUMER
        assert published.context is not None and consumed.context is not None
        assert consumed.context.trace_id == published.context.trace_id
        assert consumed.parent is not None and consumed.parent.span_id == published.context.span_id
        assert not trace.get_current_span().get_span_context().is_valid
        app.close()


@pytest.mark.parametrize("headers", [{}, {"traceparent": "bad", "baggage": SECRET}])
def test_worker_independent_context_and_retry_metrics(headers: dict[str, str]) -> None:
    with memory("fleetlink-worker") as (runtime, exporter, reader):
        with create_celery(Settings(celery_enabled=True), telemetry=TelemetrySlot(runtime)) as app:
            task = app.tasks[TASK_NAME]
            payload = ProbePayload(probe_id=str(uuid4()), value=2, failures_before_success=2)
            result = task.apply(args=(payload.model_dump(),), headers=headers, throw=False)
            assert result.result == {"probe_id": payload.probe_id, "value": 4, "attempts": 3}
            with pytest.raises(InvalidProbe):
                task.apply(args=({"invalid": SECRET},), headers=headers, throw=True)
            data = reader.get_metrics_data()
            assert data is not None
            metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
            assert metric.name == "fleetlink.celery.task.duration" and metric.unit == "s"
            assert metric.description == "Technical Celery attempt duration"
            assert isinstance(metric.data, Histogram)
            outcomes = {
                (point.attributes or {})["outcome"]: point.count
                for point in metric.data.data_points
            }
            assert outcomes == {"retry": 2, "success": 1, "failure": 1}
            assert SECRET not in data.to_json()
            for point in metric.data.data_points:
                assert set(point.attributes or {}) == {"celery.task.name", "outcome"}
            for span in exporter.get_finished_spans():
                assert span.parent is None
                assert span.context is not None and span.context.is_valid
                assert SECRET not in repr(span.attributes)
                assert span.events == ()
            assert correlation_id.get() is None


def test_sql_events_capture_no_queries_parameters_or_urls() -> None:
    # SQLite is only an event-driver fixture, never PostgreSQL integration evidence.
    engine = create_engine("sqlite://")
    with memory() as (runtime, exporter, _):
        instrumentation = DatabaseInstrumentation(engine, runtime)
        with runtime.operation("parent") as parent:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT :secret"), {"secret": SECRET}) == SECRET
                with pytest.raises(OperationalError):
                    connection.execute(text("SELECT missing_synthetic_column"))
        spans = exporter.get_finished_spans()
        assert len(spans) == 3
        for span in spans[:2]:
            assert span.name == "postgresql.query"
            assert span.attributes == {"db.system.name": "postgresql"}
            assert span.parent == parent.get_span_context()
            assert span.events == ()
        assert spans[1].status.status_code == trace.StatusCode.ERROR
        instrumentation.close()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        assert len(exporter.get_finished_spans()) == 3
    engine.dispose()


def test_redis_spans_omit_keys_values_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(runtime: Telemetry) -> None:
        client = AsyncMock()
        client.get.return_value = SECRET
        monkeypatch.setattr("fleetlink.infrastructure.redis.Redis", lambda **kwargs: client)
        redis = TechnicalRedis(Settings(redis_password=SecretStr(SECRET)))
        redis.telemetry = runtime
        identifier = uuid4()
        await redis.ping()
        await redis.write(identifier, SECRET)
        assert await redis.read(identifier) == SECRET
        await redis.delete(identifier)
        client.get.side_effect = TimeoutError(SECRET)
        with pytest.raises(RuntimeError):
            await redis.read(identifier)
        await redis.close()

    with memory() as (runtime, exporter, _):
        asyncio.run(run(runtime))
        spans = exporter.get_finished_spans()
        assert [span.name for span in spans] == [
            "redis.PING",
            "redis.SET",
            "redis.GET",
            "redis.DEL",
            "redis.GET",
        ]
        for span in spans:
            assert span.attributes == {"db.system.name": "redis"}
            assert span.events == ()
        assert spans[-1].status.status_code == trace.StatusCode.ERROR


def test_repeated_lifespan_owns_fresh_resources() -> None:
    instances: list[Telemetry] = []
    exporters: list[Mock] = []

    def factory(settings: Settings, service: str) -> Telemetry:
        exporter = Mock(spec=SpanExporter, export=Mock(return_value=SpanExportResult.SUCCESS))
        runtime = Telemetry(settings, service, span_exporter=exporter)
        instances.append(runtime)
        exporters.append(exporter)
        return runtime

    app = create_app(Settings(), telemetry_factory=factory)
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/openapi.json").status_code == 200
        assert app.state.telemetry is None
        assert not app.state.readiness.initialized
    assert instances[0] is not instances[1]
    for exporter in exporters:
        exporter.shutdown.assert_called_once()
    instances[0].shutdown()
    exporters[0].shutdown.assert_called_once()


def test_exporter_failure_isolation_and_sanitized_logs(caplog: pytest.LogCaptureFixture) -> None:
    exporter = Mock(
        spec=SpanExporter,
        export=Mock(side_effect=RuntimeError(SECRET)),
        shutdown=Mock(side_effect=RuntimeError(SECRET)),
    )
    runtime = Telemetry(Settings(), "fleetlink-api", span_exporter=exporter)
    app = create_app(Settings(), telemetry_factory=lambda settings, service: runtime)
    with caplog.at_level(logging.WARNING, logger="fleetlink.telemetry"):
        with TestClient(app) as client:
            assert client.get("/openapi.json").status_code == 200
            assert client.get("/absent").status_code == 404
            assert client.get("/ready").status_code == 200
        assert SECRET not in caplog.text
        assert [record.message for record in caplog.records] == ["telemetry_export_failed"]


def test_batch_shutdown_flushes_and_is_bounded() -> None:
    entered = Event()
    release = Event()
    exited = Event()

    class BlockingExporter(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            entered.set()
            assert release.wait(5)
            exited.set()
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            release.set()

    provider = TracerProvider(shutdown_on_exit=False)
    processor = DeadlineBatchSpanProcessor(SafeSpanExporter(BlockingExporter()), 0.05)
    provider.add_span_processor(processor)
    with provider.get_tracer("test").start_as_current_span("pending"):
        pass
    start = monotonic()
    provider.shutdown()
    assert monotonic() - start < 1
    assert entered.is_set() and exited.wait(1)


def test_otlp_export_path_startup_failure_privacy_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    transports: list[Mock] = []

    def span_factory(
        *, endpoint: str, timeout: float, compression: Compression
    ) -> OTLPSpanExporter:
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout, compression=compression)
        transport = Mock(
            request=Mock(
                return_value=Mock(error=RuntimeError(SECRET), status_code=None, reason=None)
            ),
            is_connection_error=Mock(return_value=False),
        )
        exporter._client._transport.close()
        exporter._client._transport = transport
        transports.append(transport)
        return exporter

    def metric_factory(
        *, endpoint: str, timeout: float, compression: Compression
    ) -> OTLPMetricExporter:
        exporter = OTLPMetricExporter(endpoint=endpoint, timeout=timeout, compression=compression)
        transport = Mock(
            request=Mock(
                return_value=Mock(error=RuntimeError(SECRET), status_code=None, reason=None)
            ),
            is_connection_error=Mock(return_value=False),
        )
        exporter._client._transport.close()
        exporter._client._transport = transport
        transports.append(transport)
        return exporter

    monkeypatch.setattr("fleetlink.observability.runtime.OTLPSpanExporter", span_factory)
    monkeypatch.setattr("fleetlink.observability.runtime.OTLPMetricExporter", metric_factory)
    settings = Settings(
        telemetry_enabled=True,
        otel_exporter_otlp_endpoint=SecretStr("http://collector.invalid:4318"),
        otel_export_timeout_seconds=0.1,
    )
    app = create_app(settings)
    assert not transports
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            assert len(transports) == 2
            for transport in transports:
                transport.request.assert_not_called()
            assert client.get("/openapi.json").status_code == 200
            assert client.get("/ready").status_code == 200
        assert SECRET not in caplog.text
        assert "collector.invalid" not in caplog.text
    urls: set[str] = set()
    for transport in transports:
        transport.close.assert_called_once()
        assert transport.request.call_count == 1
        call = transport.request.call_args
        urls.add(call.args[1])
        assert 0 < call.kwargs["timeout"] <= 0.1
        assert "authorization" not in call.kwargs["headers"]
        assert "cookie" not in call.kwargs["headers"]
    assert urls == {
        "http://collector.invalid:4318/v1/traces",
        "http://collector.invalid:4318/v1/metrics",
    }


def test_metric_export_failure_isolated() -> None:
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData

    from fleetlink.observability.export import SafeMetricExporter

    delegate = Mock(
        spec=MetricExporter,
        export=Mock(side_effect=RuntimeError(SECRET)),
        shutdown=Mock(side_effect=RuntimeError(SECRET)),
    )
    exporter = SafeMetricExporter(delegate)
    assert exporter.export(MetricsData([])) == MetricExportResult.FAILURE
    exporter.shutdown()


def test_worker_child_lifecycle_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery import signals

    from fleetlink import worker

    runtime = Mock(spec=Telemetry)
    constructed = Mock(return_value=runtime)
    celery = Mock()
    celery.Worker.return_value.exitcode = 0

    def start() -> None:
        assert constructed.call_count == 0
        signals.worker_process_init.send(sender=celery)
        constructed.assert_called_once()
        assert constructed.call_args.args[1] == "fleetlink-worker"
        signals.worker_process_shutdown.send(sender=celery)
        runtime.shutdown.assert_called_once()

    celery.Worker.return_value.start.side_effect = start
    monkeypatch.setattr(worker, "Settings", lambda: Settings(celery_enabled=True))
    monkeypatch.setattr(worker, "create_celery", lambda settings, **kwargs: celery)
    monkeypatch.setattr(worker, "Telemetry", constructed)
    worker.main()
    celery.close.assert_called_once()
    signals.worker_process_init.send(sender=celery)
    assert constructed.call_count == 1  # signal receivers disconnected by main cleanup


def test_partial_startup_closes_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = Mock(spec=SpanExporter)
    runtime = Telemetry(Settings(), "fleetlink-api", span_exporter=exporter)
    monkeypatch.setattr("fleetlink.main.TechnicalRedis", Mock(side_effect=RuntimeError("fixture")))
    app = create_app(
        Settings(redis_enabled=True), telemetry_factory=lambda settings, service: runtime
    )
    with pytest.raises(RuntimeError, match="fixture"), TestClient(app):
        pytest.fail("must fail during startup")
    exporter.shutdown.assert_called_once()
    assert app.state.telemetry is None


def test_http_cancellation_cleans_trace_context() -> None:
    async def run(runtime: Telemetry) -> None:
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            assert trace.get_current_span().get_span_context().is_valid
            raise asyncio.CancelledError()

        async def receive() -> Message:
            raise AssertionError("must not consume body")

        async def send(message: Message) -> None:
            pass

        outer = TelemetryMiddleware(RequestContextMiddleware(app))
        scope: Scope = {
            "type": "http",
            "path": "/cancel",
            "method": "GET",
            "headers": [],
            "app": Mock(state=Mock(telemetry=runtime)),
        }
        with pytest.raises(asyncio.CancelledError):
            await outer(scope, receive, send)
        assert correlation_id.get() is None
        assert not trace.get_current_span().get_span_context().is_valid

    with memory() as (runtime, exporter, _):
        asyncio.run(run(runtime))
        assert exporter.get_finished_spans()[0].status.status_code == trace.StatusCode.ERROR


def test_sub_millisecond_timeout_can_initialize_and_shutdown() -> None:
    exporter = InMemorySpanExporter()
    processor = DeadlineBatchSpanProcessor(SafeSpanExporter(exporter), 0.000001)
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(processor)
    provider.shutdown()
    runtime = Telemetry(Settings(otel_export_timeout_seconds=0.000001), "fleetlink-api")
    runtime.shutdown()


def test_producer_cleanup_failure_still_closes_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    producer = TechnicalProducer(Settings(celery_enabled=True))
    runtime = Mock(spec=Telemetry)
    producer.telemetry = runtime
    close_app = producer.app.close
    monkeypatch.setattr(producer.app, "close", Mock(side_effect=RuntimeError("fixture")))
    try:
        with pytest.raises(RuntimeError, match="fixture"):
            producer.close()
        runtime.shutdown.assert_called_once()
    finally:
        close_app()


def test_http_validation_and_method_errors_preserve_problem_contract() -> None:
    with memory() as (runtime, exporter, _):
        app = create_app(Settings(), telemetry_factory=lambda settings, service: runtime)

        @app.get("/number/{value}")
        async def number(value: int) -> dict[str, int]:
            return {"value": value}

        with TestClient(app) as client:
            invalid = client.get(f"/number/{SECRET}", headers={"x-correlation-id": "validated-id"})
            problem = Problem.model_validate(invalid.json())
            assert invalid.status_code == 422 and problem.code == "validation_error"
            assert problem.correlation_id == "validated-id"
            assert SECRET not in invalid.text
            method = client.post("/health")
            assert method.status_code == 405
            assert method.headers["allow"] == "GET"
            assert Problem.model_validate(method.json()).code == "http_405"
        span = exporter.get_finished_spans()[0]
        assert span.name == "GET /number/{value}"
        assert (span.attributes or {})["http.response.status_code"] == 422


def test_late_sdk_override_cannot_load_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        telemetry_enabled=True,
        otel_exporter_otlp_endpoint=SecretStr("http://localhost:4318"),
    )
    app = create_app(settings)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={SECRET}")
    factory = Mock(side_effect=AssertionError("must reject before exporter creation"))
    monkeypatch.setattr("fleetlink.observability.runtime.OTLPSpanExporter", factory)
    with pytest.raises(ValueError, match="Unsupported OpenTelemetry") as caught, TestClient(app):
        pytest.fail("locally invalid configuration must fail")
    assert SECRET not in str(caught.value)
    factory.assert_not_called()
    assert app.state.telemetry is None


def test_failure_after_response_start_preserves_sent_status_and_cleans_context() -> None:
    async def run(runtime: Telemetry) -> None:
        messages: list[Message] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise RuntimeError(SECRET)

        async def receive() -> Message:
            raise AssertionError("must not consume body")

        async def send(message: Message) -> None:
            messages.append(message)

        outer = TelemetryMiddleware(RequestContextMiddleware(app))
        scope: Scope = {
            "type": "http",
            "path": "/partial",
            "method": "GET",
            "headers": [],
            "app": Mock(state=Mock(telemetry=runtime)),
        }
        with pytest.raises(RuntimeError):
            await outer(scope, receive, send)
        assert len(messages) == 1 and messages[0]["status"] == 200
        assert correlation_id.get() is None
        assert not trace.get_current_span().get_span_context().is_valid

    with memory() as (runtime, exporter, reader):
        asyncio.run(run(runtime))
        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == trace.StatusCode.ERROR
        assert (span.attributes or {})["http.response.status_code"] == 200
        data = reader.get_metrics_data()
        assert data is not None
        point = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
        assert (point.attributes or {})["http.response.status_class"] == "2xx"


def test_missing_version_metadata_does_not_prevent_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fleetlink.observability.runtime.version", Mock(side_effect=PackageNotFoundError)
    )
    with memory() as (runtime, exporter, _):
        app = create_app(Settings(), telemetry_factory=lambda settings, service: runtime)
        with TestClient(app) as client:
            assert client.get("/openapi.json").status_code == 200
        assert dict(exporter.get_finished_spans()[0].resource.attributes) == {
            "service.name": "fleetlink-api",
            "deployment.environment.name": "test",
        }


def test_sdk_diagnostics_use_existing_json_sink_and_keep_severity() -> None:
    from fleetlink.observability.export import ExportLogFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("fleetlink.telemetry")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        boundary = ExportLogFilter()
        record = logging.LogRecord(
            "opentelemetry.exporter", logging.ERROR, "", 0, f"failed: {SECRET}", (), None
        )
        assert not boundary.filter(record)
        payload = json.loads(stream.getvalue())
        assert payload["event"] == "telemetry_runtime_event"
        assert payload["level"] == "ERROR"
        assert SECRET not in stream.getvalue()
        assert not boundary.filter(record)
        assert len(stream.getvalue().splitlines()) == 1
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
