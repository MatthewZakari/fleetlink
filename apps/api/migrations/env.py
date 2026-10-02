"""Async online migrations; no application startup migration side effects."""

import asyncio
import os

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import metadata
from fleetlink.modules.identity.infrastructure import models as identity_models  # noqa: F401


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        # Only explicitly registered tables belong to application autogeneration.
        # This excludes PostGIS and unrelated tables; deliberate drops need review.
        include_name=lambda name, kind, parent: kind != "table" or name in metadata.tables,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    if not os.environ.get("FLEETLINK_POSTGRES_DB"):
        raise CommandError("Set FLEETLINK_POSTGRES_DB explicitly before running migrations")
    settings = Settings()
    engine = create_async_engine(
        settings.database_url(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "timeout": settings.database_connect_timeout,
            "command_timeout": settings.database_command_timeout,
        },
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    except CommandError:
        raise
    except Exception:
        # Driver authentication errors can contain role names. Never print raw errors.
        raise CommandError("Migration failed; verify database access and prerequisites") from None
    finally:
        try:
            await engine.dispose()
        except Exception:
            raise CommandError("Migration connection cleanup failed") from None


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
