"""Explicit PostgreSQL suite. Never collected by the default tests directory."""

import asyncio
import socket
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.pool import AsyncAdaptedQueuePool

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database, DatabaseError

TEST_DATABASE = "fleetlink_test_fl005"


def test_async_postgres_observability_privacy(settings: Settings) -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from fleetlink.observability import Telemetry
    from fleetlink.observability.database import DatabaseInstrumentation

    async def run() -> None:
        exporter = InMemorySpanExporter()
        runtime = Telemetry(settings, "fleetlink-api", span_exporter=exporter)
        database = Database(settings)
        instrumentation = DatabaseInstrumentation(database.engine.sync_engine, runtime)
        try:
            with runtime.operation("integration") as parent:
                async with database.connection() as connection:
                    assert (
                        await connection.scalar(
                            text("SELECT CAST(:value AS TEXT)"), {"value": "synthetic-fl007-secret"}
                        )
                        == "synthetic-fl007-secret"
                    )
            queries = [
                span for span in exporter.get_finished_spans() if span.name == "postgresql.query"
            ]
            assert queries
            for span in queries:
                assert span.attributes == {"db.system.name": "postgresql"}
                assert span.parent == parent.get_span_context()
                assert span.events == ()
        finally:
            instrumentation.close()
            await database.dispose()
            runtime.shutdown()

    asyncio.run(run())


def test_async_postgres_transactions_and_cleanup(settings: Settings) -> None:
    async def run() -> None:
        database = Database(settings)
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        try:
            async with database.connection() as connection:
                row = (
                    await connection.execute(text("SELECT current_database(), current_user"))
                ).one()
                assert row[0] == TEST_DATABASE
                assert row[1] == settings.postgres_user.get_secret_value()
                assert await connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
                )
                assert "POSTGIS=" in str(
                    await connection.scalar(text("SELECT PostGIS_Full_Version()"))
                )
                row = (
                    await connection.execute(
                        text(
                            "SELECT ST_SRID(p), ST_AsText(p), "
                            "ST_Distance(p, ST_SetSRID(ST_MakePoint(3, 7), 4326)) "
                            "FROM (SELECT ST_SetSRID(ST_MakePoint(3, 6), 4326) AS p) point"
                        )
                    )
                ).one()
                assert tuple(row) == (4326, "POINT(3 6)", 1)

            # Temporary objects persist only on this single pooled backend connection.
            async with database.session() as session:
                async with session.begin():
                    await session.execute(text("CREATE TEMP TABLE fl005_probe (value integer)"))
                    await session.execute(text("INSERT INTO fl005_probe VALUES (1)"))
            assert pool.checkedout() == 0

            # Closing an unfinished session must roll back, never auto-commit.
            async with database.session() as session:
                await session.begin()
                await session.execute(text("INSERT INTO fl005_probe VALUES (300)"))
            async with database.session() as session:
                async with session.begin():
                    assert await session.scalar(text("SELECT sum(value) FROM fl005_probe")) == 1
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as session:
                    async with session.begin():
                        await session.execute(text("INSERT INTO fl005_probe VALUES (100)"))
                        raise RuntimeError("injected")
            async with database.session() as session:
                async with session.begin():
                    assert await session.scalar(text("SELECT sum(value) FROM fl005_probe")) == 1
            assert pool.checkedout() == 0

            acquired = asyncio.Event()

            async def cancelled_work() -> None:
                async with database.session() as session:
                    async with session.begin():
                        await session.execute(text("INSERT INTO fl005_probe VALUES (200)"))
                        acquired.set()
                        await asyncio.Event().wait()

            task = asyncio.create_task(cancelled_work())
            await asyncio.wait_for(acquired.wait(), timeout=10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert pool.checkedout() == 0
            async with database.session() as session:
                async with session.begin():
                    assert await session.scalar(text("SELECT sum(value) FROM fl005_probe")) == 1
        finally:
            await database.dispose()
        assert pool.checkedout() == 0
        assert pool.checkedin() == 0

    asyncio.run(run())


def test_connection_failure(settings: Settings) -> None:
    async def run() -> None:
        # Reserve a local port without listening: deterministic connection refusal.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            failed = Database(
                settings.model_copy(
                    update={
                        "postgres_host": "127.0.0.1",
                        "postgres_port": reserved.getsockname()[1],
                    }
                )
            )
            try:
                with pytest.raises(DatabaseError):
                    async with failed.connection():
                        pytest.fail("Unexpected connection")
            finally:
                await failed.dispose()

    asyncio.run(run())


def test_migration_round_trip(settings: Settings, migrated_database: None) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))

    async def revision() -> str | None:
        database = Database(settings)
        try:
            async with database.connection() as connection:
                value = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                assert await connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')")
                )
                return str(value) if value else None
        finally:
            await database.dispose()

    command.upgrade(config, "head")
    assert asyncio.run(revision()) == "0002_identity_foundation"
    command.upgrade(config, "head")
    assert asyncio.run(revision()) == "0002_identity_foundation"

    async def unrelated_object(*, create: bool) -> None:
        database = Database(settings)
        try:
            async with database.connection() as connection, connection.begin():
                if create:
                    await connection.execute(text("CREATE TABLE fl005_migration_probe (value int)"))
                    await connection.execute(text("INSERT INTO fl005_migration_probe VALUES (42)"))
                else:
                    assert (
                        await connection.scalar(text("SELECT value FROM fl005_migration_probe"))
                        == 42
                    )
                    await connection.execute(text("DROP TABLE fl005_migration_probe"))
        finally:
            await database.dispose()

    asyncio.run(unrelated_object(create=True))
    command.check(config)
    command.downgrade(config, "0001_technical_baseline")
    assert asyncio.run(revision()) == "0001_technical_baseline"

    async def identity_absent() -> None:
        database = Database(settings)
        try:
            async with database.connection() as connection:
                for table in ("identity_users", "identity_user_roles"):
                    assert (
                        await connection.scalar(
                            text("SELECT to_regclass(:table)"), {"table": table}
                        )
                        is None
                    )
        finally:
            await database.dispose()

    asyncio.run(identity_absent())
    command.upgrade(config, "head")
    assert asyncio.run(revision()) == "0002_identity_foundation"
    command.check(config)
    command.downgrade(config, "base")
    assert asyncio.run(revision()) is None
    command.upgrade(config, "head")
    assert asyncio.run(revision()) == "0002_identity_foundation"
    command.check(config)
    asyncio.run(unrelated_object(create=False))


def test_authentication_failure_is_sanitized(settings: Settings) -> None:
    async def run() -> None:
        secret = "synthetic-invalid-password:@/%"
        database = Database(settings.model_copy(update={"postgres_password": SecretStr(secret)}))
        try:
            with pytest.raises(DatabaseError) as caught:
                async with database.connection():
                    pytest.fail("Invalid password unexpectedly authenticated")
            assert secret not in str(caught.value)
            assert settings.postgres_user.get_secret_value() not in str(caught.value)
        finally:
            await database.dispose()

    asyncio.run(run())
