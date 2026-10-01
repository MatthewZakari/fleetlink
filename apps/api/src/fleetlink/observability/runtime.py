"""OpenTelemetry composition, safe operations and bounded technical metrics."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter

from celery.exceptions import Retry
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from fleetlink.core.config import Settings, reject_otel_overrides
from fleetlink.observability.export import (
    DeadlineBatchSpanProcessor,
    FailureLog,
    SafeMetricExporter,
    SafeSpanExporter,
    sanitize_sdk_logs,
)

propagator = TraceContextTextMapPropagator()


def extract(headers: Mapping[str, object] | None) -> Context:
    # Allow only a bounded traceparent, drop baggage and tracestate (vendor/untrusted data).
    value = headers.get("traceparent") if headers else None
    carrier = {"traceparent": value} if isinstance(value, str) and len(value) <= 256 else {}
    return propagator.extract(carrier, context=Context())


def inject() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagator.inject(carrier)
    return {key: value for key, value in carrier.items() if key == "traceparent"}


@dataclass
class TelemetrySlot:
    """Worker resources are assigned after fork, never inherited from the parent."""

    current: "Telemetry | None" = None


class Telemetry:
    def __init__(
        self,
        settings: Settings,
        service: str,
        *,
        span_exporter: SpanExporter | None = None,
        metric_reader: MetricReader | None = None,
    ) -> None:
        self.redactor = settings.redactor()
        self._closed = False
        self._timeout_ms = max(1, int(settings.otel_export_timeout_seconds * 1000))
        self.traces: TracerProvider | None = None
        self.metrics: MeterProvider | None = None
        try:
            service_version = version("fleetlink-api")
        except PackageNotFoundError:
            service_version = None
        resource_attributes = {
            "service.name": self.redactor.text(service),
            "deployment.environment.name": settings.environment,
        }
        if service_version is not None:
            resource_attributes["service.version"] = service_version
        self.resource = Resource(resource_attributes)
        active = (
            settings.telemetry_enabled or span_exporter is not None or metric_reader is not None
        )
        readers: list[MetricReader] = []
        if settings.telemetry_enabled and span_exporter is None and metric_reader is None:
            # Exporters consult native variables at construction, potentially long
            # after Settings validation. Reject late overrides before that boundary.
            reject_otel_overrides()
            sanitize_sdk_logs()
            endpoint = settings.otel_exporter_otlp_endpoint
            assert endpoint is not None  # validated Settings
            base = endpoint.get_secret_value().rstrip("/")
            span_exporter = OTLPSpanExporter(
                endpoint=f"{base}/v1/traces",
                timeout=settings.otel_export_timeout_seconds,
                compression=Compression.NoCompression,
            )
            metric_exporter = SafeMetricExporter(
                OTLPMetricExporter(
                    endpoint=f"{base}/v1/metrics",
                    timeout=settings.otel_export_timeout_seconds,
                    compression=Compression.NoCompression,
                )
            )
            readers.append(
                PeriodicExportingMetricReader(
                    metric_exporter,
                    export_interval_millis=60_000,
                    export_timeout_millis=self._timeout_ms,
                )
            )
            batch = True
        else:
            batch = False
            if metric_reader is not None:
                readers.append(metric_reader)
        if active:
            self.traces = TracerProvider(
                resource=self.resource,
                sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
                shutdown_on_exit=False,
            )
            if span_exporter is not None:
                safe_exporter = SafeSpanExporter(span_exporter)
                self.traces.add_span_processor(
                    DeadlineBatchSpanProcessor(safe_exporter, settings.otel_export_timeout_seconds)
                    if batch
                    else SimpleSpanProcessor(safe_exporter)
                )
            self.metrics = MeterProvider(
                resource=self.resource,
                metric_readers=readers,
                shutdown_on_exit=False,
                views=[
                    View(
                        instrument_name="http.server.request.duration",
                        attribute_keys={
                            "http.request.method",
                            "http.route",
                            "http.response.status_class",
                        },
                    ),
                    View(
                        instrument_name="fleetlink.celery.task.duration",
                        attribute_keys={"celery.task.name", "outcome"},
                    ),
                ],
            )
            self.tracer = self.traces.get_tracer("fleetlink", service_version)
            meter = self.metrics.get_meter("fleetlink", service_version)
        else:
            self.tracer = trace.NoOpTracerProvider().get_tracer("fleetlink")
            meter = metrics.NoOpMeterProvider().get_meter("fleetlink")
        self.http_duration = meter.create_histogram(
            "http.server.request.duration", unit="s", description="HTTP server request duration"
        )
        self.task_duration = meter.create_histogram(
            "fleetlink.celery.task.duration",
            unit="s",
            description="Technical Celery attempt duration",
        )

    @contextmanager
    def operation(
        self,
        name: str,
        *,
        kind: trace.SpanKind = trace.SpanKind.CLIENT,
        parent: Context | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> Iterator[trace.Span]:
        # SDK default exception events contain exception messages and stack traces.
        with self.tracer.start_as_current_span(
            self.redactor.text(name),
            kind=kind,
            context=parent,
            attributes={key: self.redactor.text(value) for key, value in attributes.items()}
            if attributes
            else None,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException:
                span.set_status(trace.StatusCode.ERROR)
                raise

    @contextmanager
    def task(self, headers: Mapping[str, object] | None, name: str) -> Iterator[None]:
        name = self.redactor.text(name)
        start = perf_counter()
        outcome = "success"
        with self.operation(
            "celery.process",
            kind=trace.SpanKind.CONSUMER,
            parent=extract(headers),
            attributes={"celery.task.name": name, "messaging.system": "rabbitmq"},
        ):
            try:
                yield
            except BaseException as error:
                outcome = "retry" if isinstance(error, Retry) else "failure"
                raise
            finally:
                self.task_duration.record(
                    perf_counter() - start, {"celery.task.name": name, "outcome": outcome}
                )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Trace processor drains/flushes within its deadline. Metric shutdown performs
        # its final collection under the configured exporter and reader deadline.
        failures = FailureLog()
        for provider in (self.traces, self.metrics):
            try:
                if isinstance(provider, TracerProvider):
                    provider.shutdown()
                elif isinstance(provider, MeterProvider):
                    provider.shutdown(timeout_millis=self._timeout_ms)
            except Exception:
                failures.report()
