"""Application factory and composition root; no business endpoints."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response

from fleetlink.core.config import Settings
from fleetlink.core.http import problem_responses, register_http
from fleetlink.core.logging import configure_logging
from fleetlink.core.readiness import (
    HealthResponse,
    Readiness,
    ReadinessResponse,
    get_readiness,
)
from fleetlink.infrastructure.database import Database
from fleetlink.infrastructure.redis import TechnicalRedis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    readiness = Readiness()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with AsyncExitStack() as resources:
            try:
                if settings.database_enabled:
                    database = Database(settings)
                    resources.push_async_callback(database.dispose)
                    app.state.database = database
                if settings.redis_enabled:
                    redis = TechnicalRedis(settings)
                    resources.push_async_callback(redis.close)
                    app.state.redis = redis
                readiness.initialized = True
                yield
            finally:
                readiness.initialized = False
                app.state.database = None
                app.state.redis = None

    app = FastAPI(
        title="FleetLink technical API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        responses=problem_responses(),
    )
    app.state.settings = settings
    app.state.readiness = readiness
    app.state.database = None
    app.state.redis = None
    register_http(app, settings.log_level)

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
