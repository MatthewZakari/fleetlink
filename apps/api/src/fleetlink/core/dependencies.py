"""Native request dependencies; no environment reads or service container."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database
from fleetlink.infrastructure.redis import TechnicalRedis


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_correlation_id(request: Request) -> str:
    request_id: str = request.state.correlation_id
    return request_id


def get_database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise RuntimeError("Database resources are disabled or outside application lifespan")
    return database


def get_redis(request: Request) -> TechnicalRedis:
    redis: TechnicalRedis | None = request.app.state.redis
    if redis is None:
        raise RuntimeError("Redis resources are disabled or outside application lifespan")
    return redis


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session
