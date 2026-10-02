"""Only the explicitly named, freshly provisioned FL-005 database is permitted."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from fleetlink.core.config import Settings
from fleetlink.infrastructure.database import Database

TEST_DATABASE = "fleetlink_test_fl005"


@pytest.fixture(scope="session")
def settings() -> Settings:
    if os.environ.get("FLEETLINK_POSTGRES_DB") != TEST_DATABASE:
        pytest.fail(
            "Set FLEETLINK_POSTGRES_DB=fleetlink_test_fl005; development databases are forbidden"
        )
    return Settings(database_pool_size=1, database_max_overflow=0)


@pytest.fixture(scope="session")
def migrated_database(settings: Settings) -> None:
    async def assert_fresh() -> None:
        database = Database(settings)
        try:
            async with database.connection() as connection:
                assert await connection.scalar(text("SELECT current_database()")) == TEST_DATABASE
                assert (
                    await connection.scalar(text("SELECT to_regclass('alembic_version')")) is None
                ), "Fresh migration requires a newly provisioned dedicated test database"
        finally:
            await database.dispose()

    asyncio.run(assert_fresh())
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
