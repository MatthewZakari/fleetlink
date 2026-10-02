"""Application-owned async resources. Construction performs no database I/O."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from anyio import CancelScope
from sqlalchemy import MetaData
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fleetlink.core.config import Settings

# Explicit infrastructure mappings register here. Never create_all at startup.
metadata = MetaData()


class DatabaseError(RuntimeError):
    """Safe adapter error; driver messages can expose connection credentials."""


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except Exception as error:
        if isinstance(error, (DBAPIError, OSError, TimeoutError)) or type(
            error
        ).__module__.startswith("asyncpg."):
            raise DatabaseError(
                "Database operation failed; verify connectivity and configuration"
            ) from None
        raise


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_pre_ping=True,
            echo=False,
            hide_parameters=True,
            connect_args={
                "timeout": settings.database_connect_timeout,
                "command_timeout": settings.database_command_timeout,
            },
        )
        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False, autobegin=False
        )

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Short-lived connection; close rolls back any uncommitted transaction."""
        with _safe_errors():
            connection = await self.engine.connect()
            try:
                yield connection
            finally:
                with CancelScope(shield=True):
                    await connection.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Acquire a session, never commit it. Caller must explicitly begin work."""
        with _safe_errors():
            session = self.sessions()
            try:
                yield session
            finally:
                # Also protect cleanup from Starlette/AnyIO level cancellation.
                with CancelScope(shield=True):
                    await session.close()

    async def dispose(self) -> None:
        """Release idle pooled connections after all sessions have closed."""
        with _safe_errors(), CancelScope(shield=True):
            await self.engine.dispose()
