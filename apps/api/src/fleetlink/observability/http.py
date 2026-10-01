"""Body-transparent pure ASGI HTTP instrumentation with bounded route attributes."""

from time import perf_counter

from opentelemetry import trace
from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fleetlink.observability.runtime import Telemetry, extract

METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
)


class TelemetryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        telemetry: Telemetry | None = scope["app"].state.telemetry
        if telemetry is None:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        method = method if method in METHODS else "_OTHER"
        status = 500
        start = perf_counter()

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        async def serve(span: trace.Span | None) -> None:
            try:
                await self.app(scope, receive, capture_status)
            finally:
                route = scope.get("route")
                template = route.path if isinstance(route, Route) else "unmatched"
                attributes = {
                    "http.request.method": method,
                    "http.route": template,
                    "http.response.status_class": f"{status // 100}xx",
                }
                if span is not None:
                    span.update_name(f"{method} {template}")
                    span.set_attribute("http.route", template)
                    span.set_attribute("http.response.status_code", status)
                    if status >= 500:
                        span.set_status(trace.StatusCode.ERROR)
                telemetry.http_duration.record(perf_counter() - start, attributes)

        if scope.get("path") in ("/health", "/ready"):
            await serve(None)
        else:
            values = Headers(scope=scope).getlist("traceparent")
            carrier = {"traceparent": values[0]} if len(values) == 1 else {}
            with telemetry.operation(
                "HTTP",
                kind=trace.SpanKind.SERVER,
                parent=extract(carrier),
                attributes={"http.request.method": method},
            ) as span:
                await serve(span)
