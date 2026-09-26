"""Technical problem responses and request-scoped correlation middleware."""

import logging
import re
from http import HTTPStatus
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fleetlink.core.logging import correlation_id

logger = logging.getLogger("fleetlink.http")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    code: str
    correlation_id: str


def problem(status: int, code: str, detail: str, request_id: str) -> JSONResponse:
    # URN avoids reflecting untrusted URLs or query strings in error responses.
    body = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=f"urn:uuid:{uuid4()}",
        code=code,
        correlation_id=request_id,
    )
    return JSONResponse(
        body.model_dump(), status_code=status, media_type="application/problem+json"
    )


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    response = problem(
        exc.status_code,
        f"http_{exc.status_code}",
        HTTPStatus(exc.status_code).phrase,
        request.state.correlation_id,
    )
    # Preserve protocol headers such as Allow without exposing exception details.
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return problem(
        422, "validation_error", "Request validation failed.", request.state.correlation_id
    )


class RequestContextMiddleware:
    """Pure ASGI middleware preserves context and covers unhandled pre-response failures."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = Headers(scope=scope).getlist("x-correlation-id")
        supplied = values[0] if len(values) == 1 else ""
        request_id = supplied if SAFE_ID.fullmatch(supplied) else str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = request_id
        token = correlation_id.set(request_id)
        started = False
        status_code = 500
        start = perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal started, status_code
            if message["type"] == "http.response.start":
                started = True
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Correlation-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            logger.error("request_failed", extra={"error_type": type(exc).__name__})
            if started:
                raise
            response = problem(500, "internal_error", "An unexpected error occurred.", request_id)
            await response(scope, receive, send_with_id)
        finally:
            logger.info(
                "request_completed",
                extra={
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                },
            )
            correlation_id.reset(token)
