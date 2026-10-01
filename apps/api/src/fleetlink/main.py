"""Application factory and composition root; no business endpoints."""

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

from anyio import CancelScope, to_thread
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
from fleetlink.observability import Telemetry
from fleetlink.observability.database import DatabaseInstrumentation
from fleetlink.observability.http import TelemetryMiddleware


def create_app(
    settings: Settings | None = None,
    *,
    telemetry_factory: Callable[[Settings, str], Telemetry] = Telemetry,
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    readiness = Readiness()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with AsyncExitStack() as resources:
            try:
                telemetry = telemetry_factory(settings, "fleetlink-api")
                app.state.telemetry = telemetry

                async def close_telemetry() -> None:
                    with CancelScope(shield=True):
                        await to_thread.run_sync(telemetry.shutdown)

                resources.push_async_callback(close_telemetry)
                if settings.database_enabled:
                    database = Database(settings)
                    resources.push_async_callback(database.dispose)
                    app.state.database = database
                    if telemetry.traces is not None:
                        instrumentation = DatabaseInstrumentation(
                            database.engine.sync_engine, telemetry
                        )
                        resources.callback(instrumentation.close)
                if settings.redis_enabled:
                    redis = TechnicalRedis(settings)
                    resources.push_async_callback(redis.close)
                    app.state.redis = redis
                    redis.telemetry = telemetry
                readiness.initialized = True
                yield
            finally:
                readiness.initialized = False
                app.state.database = None
                app.state.redis = None
                app.state.telemetry = None

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
    app.state.telemetry = None
    register_http(app, settings.log_level)
    app.add_middleware(TelemetryMiddleware)

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
