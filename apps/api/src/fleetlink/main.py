"""Application factory and composition root; no business endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from fleetlink.core.config import Settings
from fleetlink.core.http import RequestContextMiddleware, http_error, validation_error
from fleetlink.core.logging import configure_logging
from fleetlink.core.readiness import (
    HealthResponse,
    Readiness,
    ReadinessResponse,
    get_readiness,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)
    readiness = Readiness()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        readiness.initialized = True
        try:
            yield
        finally:
            readiness.initialized = False

    app = FastAPI(
        title="FleetLink technical API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness = readiness
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(HTTPException, http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]

    @app.get("/health", response_model=HealthResponse, tags=["technical"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse, "description": "Application not initialized"}},
        tags=["technical"],
    )
    async def ready(
        response: Response, state: Annotated[Readiness, Depends(get_readiness)]
    ) -> ReadinessResponse:
        snapshot = state.snapshot()
        if snapshot.status == "not_ready":
            response.status_code = 503
        return snapshot

    return app
