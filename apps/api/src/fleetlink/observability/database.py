"""Per-engine SQLAlchemy events: operation spans, no statement/parameters/URL capture."""

from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext

from fleetlink.observability.runtime import Telemetry


class DatabaseInstrumentation:
    def __init__(self, engine: Engine, telemetry: Telemetry) -> None:
        self.engine = engine
        self.telemetry = telemetry
        self.listeners: tuple[tuple[str, Callable[..., None]], ...] = (
            ("before_cursor_execute", self.before),
            ("after_cursor_execute", self.after),
            ("handle_error", self.error),
        )
        listener: Callable[..., None]
        for name, listener in self.listeners:
            event.listen(engine, name, listener)

    def before(
        self,
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        execution: ExecutionContext,
        many: bool,
    ) -> None:
        execution.__dict__["fleetlink_span"] = self.telemetry.tracer.start_span(
            "postgresql.query",
            kind=trace.SpanKind.CLIENT,
            attributes={"db.system.name": "postgresql"},
        )

    @staticmethod
    def finish(execution: ExecutionContext | None, failed: bool = False) -> None:
        if execution is None:
            return
        span: trace.Span | None = execution.__dict__.pop("fleetlink_span", None)
        if span is not None:
            if failed:
                span.set_status(trace.StatusCode.ERROR)
            span.end()

    def after(
        self,
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        execution: ExecutionContext,
        many: bool,
    ) -> None:
        self.finish(execution)

    def error(self, error: ExceptionContext) -> None:
        self.finish(error.execution_context, failed=True)

    def close(self) -> None:
        listener: Callable[..., None]
        for name, listener in self.listeners:
            event.remove(self.engine, name, listener)
