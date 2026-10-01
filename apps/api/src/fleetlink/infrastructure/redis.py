"""Lazy async Redis resources for UUID-addressed ephemeral technical probes only."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from uuid import UUID

from anyio import CancelScope
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from fleetlink.core.config import Settings
from fleetlink.observability import Telemetry


class RedisError(RuntimeError):
    """Sanitized Redis connectivity failure, distinct from programming errors."""


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError):
        raise RedisError("Redis operation failed; verify connectivity and configuration") from None


class TechnicalRedis:
    def __init__(self, settings: Settings) -> None:
        self.telemetry: Telemetry | None = None
        self._timeout = settings.redis_operation_timeout
        self._closed = False
        self._client = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            username=(
                settings.redis_username.get_secret_value() if settings.redis_username else None
            ),
            password=(
                settings.redis_password.get_secret_value() if settings.redis_password else None
            ),
            max_connections=settings.redis_max_connections,
            socket_connect_timeout=settings.redis_connect_timeout,
            socket_timeout=settings.redis_operation_timeout,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Redis resources are closed")

    @staticmethod
    def _key(identifier: UUID) -> str:
        if not isinstance(identifier, UUID):
            raise ValueError("Technical Redis keys require UUID identifiers")
        return f"fleetlink:technical:fl006:{identifier}"

    async def ping(self) -> bool:
        self._check_open()
        operation = (
            self.telemetry.operation("redis.PING", attributes={"db.system.name": "redis"})
            if self.telemetry is not None
            else nullcontext()
        )
        with operation, _safe_errors():
            async with asyncio.timeout(self._timeout):
                return bool(await self._client.ping())

    async def write(self, identifier: UUID, value: str, *, ttl_seconds: int = 60) -> None:
        self._check_open()
        key = self._key(identifier)
        if not isinstance(value, str) or len(value.encode("utf-8")) > 256:
            raise ValueError("Technical Redis values must be strings of at most 256 bytes")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("Technical Redis TTL must be between 1 and 300 seconds")
        operation = (
            self.telemetry.operation("redis.SET", attributes={"db.system.name": "redis"})
            if self.telemetry is not None
            else nullcontext()
        )
        with operation, _safe_errors():
            async with asyncio.timeout(self._timeout):
                await self._client.set(key, value, ex=ttl_seconds)

    async def read(self, identifier: UUID) -> str | None:
        self._check_open()
        key = self._key(identifier)
        operation = (
            self.telemetry.operation("redis.GET", attributes={"db.system.name": "redis"})
            if self.telemetry is not None
            else nullcontext()
        )
        with operation, _safe_errors():
            async with asyncio.timeout(self._timeout):
                value = await self._client.get(key)
                return str(value) if value is not None else None

    async def delete(self, identifier: UUID) -> None:
        self._check_open()
        key = self._key(identifier)
        operation = (
            self.telemetry.operation("redis.DEL", attributes={"db.system.name": "redis"})
            if self.telemetry is not None
            else nullcontext()
        )
        with operation, _safe_errors():
            async with asyncio.timeout(self._timeout):
                await self._client.delete(key)

    async def close(self) -> None:
        self._closed = True
        with _safe_errors(), CancelScope(shield=True):
            async with asyncio.timeout(self._timeout):
                await self._client.aclose()
