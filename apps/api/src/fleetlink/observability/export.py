"""Sanitized, rate-limited exporter failure boundary."""

import logging
from collections.abc import Sequence
from threading import Lock
from time import monotonic

from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

logger = logging.getLogger("fleetlink.telemetry")


class FailureLog:
    def __init__(self) -> None:
        self._lock = Lock()
        self._next = 0.0

    def report(self) -> None:
        with self._lock:
            now = monotonic()
            if now < self._next:
                return
            self._next = now + 60
        logger.warning("telemetry_export_failed")


class ExportLogFilter(logging.Filter):
    """Keep SDK diagnostics visible without endpoints, response text or tracebacks."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._next = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        with self._lock:
            now = monotonic()
            if now < self._next:
                return False
            self._next = now + 60
        record.msg = "telemetry_runtime_event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        logger.log(record.levelno, "telemetry_runtime_event")
        return False  # Route the safe event through FleetLink's existing JSON sink.


def sanitize_sdk_logs() -> None:
    # Logger filters do not apply to child loggers; install on each emitting namespace.
    for name in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        "opentelemetry.exporter.otlp.proto.http._common",
        "opentelemetry.sdk._shared_internal",
        "opentelemetry.sdk.metrics._internal.export",
    ):
        target = logging.getLogger(name)
        if not any(isinstance(item, ExportLogFilter) for item in target.filters):
            target.addFilter(ExportLogFilter())


class SafeSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter) -> None:
        self.delegate = delegate
        self.failures = FailureLog()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self.delegate.export(spans)
        except Exception:
            # Exporters are auxiliary plugins; never propagate their errors into requests.
            result = SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            self.failures.report()
        return result

    def shutdown(self) -> None:
        try:
            self.delegate.shutdown()
        except Exception:
            self.failures.report()


class SafeMetricExporter(MetricExporter):
    def __init__(self, delegate: MetricExporter) -> None:
        super().__init__()
        self.delegate = delegate
        self.failures = FailureLog()

    def export(
        self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs: object
    ) -> MetricExportResult:
        try:
            result = self.delegate.export(metrics_data, timeout_millis=timeout_millis)
        except Exception:
            result = MetricExportResult.FAILURE
        if result != MetricExportResult.SUCCESS:
            self.failures.report()
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True  # OTLP exporter buffers nothing; reader owns collection.

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        try:
            self.delegate.shutdown(timeout_millis=timeout_millis)
        except Exception:
            self.failures.report()


class DeadlineBatchSpanProcessor(BatchSpanProcessor):
    def __init__(self, exporter: SpanExporter, timeout_seconds: float) -> None:
        super().__init__(
            exporter,
            max_queue_size=512,
            max_export_batch_size=128,
            schedule_delay_millis=5000,
            export_timeout_millis=max(1, int(timeout_seconds * 1000)),
        )
        self._deadline_ms = max(1, int(timeout_seconds * 1000))

    def shutdown(self) -> None:
        # 1.45's public shutdown hardcodes 30s; force_flush ignores its timeout.
        # Pin/revalidate this single SDK hook on upgrades. Shutdown drains the queue.
        self._batch_processor.shutdown(timeout_millis=self._deadline_ms)
