"""Persistence configuration and lifecycle, with no network access."""

import asyncio
import io
import runpy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import InvalidRequestError

from fleetlink.core.config import Settings
from fleetlink.core.dependencies import get_database
from fleetlink.infrastructure.database import Database
from fleetlink.main import create_app


def test_url_preserves_reserved_credentials() -> None:
    user, password = "role:@/%?#", "synthetic:@/%?#"
    settings = Settings(postgres_user=SecretStr(user), postgres_password=SecretStr(password))
    url = settings.database_url()
    assert url.drivername == "postgresql+asyncpg"
    assert url.username == user
    assert url.password == password
    assert password not in repr(settings)
    assert user not in repr(settings)
    assert password not in repr(url)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POSTGRES_PORT", "0"),
        ("POSTGRES_PORT", "65536"),
        ("DATABASE_POOL_SIZE", "0"),
        ("DATABASE_POOL_SIZE", "51"),
        ("DATABASE_MAX_OVERFLOW", "-1"),
        ("DATABASE_MAX_OVERFLOW", "51"),
        ("DATABASE_POOL_TIMEOUT", "0"),
        ("DATABASE_POOL_TIMEOUT", "nan"),
        ("DATABASE_CONNECT_TIMEOUT", "inf"),
        ("DATABASE_CONNECT_TIMEOUT", "61"),
        ("DATABASE_COMMAND_TIMEOUT", "0"),
        ("DATABASE_COMMAND_TIMEOUT", "301"),
    ],
)
def test_invalid_numeric_configuration(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(f"FLEETLINK_{key}", value)
    with pytest.raises(ValidationError):
        create_app()


def test_dotenv_is_not_implicitly_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("FLEETLINK_POSTGRES_PASSWORD=unwanted-secret\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLEETLINK_POSTGRES_PASSWORD", raising=False)
    assert Settings().postgres_password.get_secret_value() != "unwanted-secret"


def test_factory_resources_owned_only_during_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    dispose = AsyncMock()
    monkeypatch.setattr(Database, "dispose", dispose)
    first = create_app(Settings(database_enabled=True))
    second = create_app(Settings(database_enabled=True))
    assert first.state.database is None
    with TestClient(first) as client, TestClient(second):
        assert first.state.database is not second.state.database
        assert client.get("/ready").json()["dependency_checks"] == "not_configured"
        assert client.get("/health").json() == {"status": "ok"}
    assert first.state.database is None
    assert dispose.await_count == 2


def test_disabled_database_creates_no_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(settings: Settings) -> None:
        pytest.fail("disabled persistence must not construct resources")

    monkeypatch.setattr("fleetlink.main.Database", fail)
    with TestClient(create_app(Settings(database_enabled=False))) as client:
        assert client.get("/health").status_code == 200


def test_sessions_require_explicit_begin_and_close_after_failure() -> None:
    async def run() -> None:
        database = Database(Settings())
        try:
            with pytest.raises(RuntimeError, match="injected"):
                async with database.session() as session:
                    assert not session.autoflush
                    with pytest.raises(InvalidRequestError, match="Autobegin"):
                        await session.connection()
                    await session.begin()
                    raise RuntimeError("injected")
            assert not session.in_transaction()
        finally:
            await database.dispose()

    asyncio.run(run())


def test_disabled_dependency_fails_explicitly() -> None:
    app = create_app(Settings(database_enabled=False))
    request = Request({"type": "http", "app": app})
    with pytest.raises(RuntimeError, match="disabled or outside"):
        get_database(request)


def test_offline_migration_has_prerequisite_check_and_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLEETLINK_POSTGRES_PASSWORD", "synthetic-migration-secret")
    monkeypatch.delenv("FLEETLINK_POSTGRES_DB", raising=False)
    output = io.StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "pg_extension" in sql
    assert "RAISE EXCEPTION" in sql
    assert "0001_technical_baseline" in sql
    assert "synthetic-migration-secret" not in sql
    assert "CREATE EXTENSION" not in sql
    assert "DROP EXTENSION" not in sql


def test_migrations_require_explicit_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEETLINK_POSTGRES_DB", raising=False)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with pytest.raises(CommandError, match="Set FLEETLINK_POSTGRES_DB explicitly"):
        command.upgrade(config, "head")


def test_baseline_rejects_missing_postgis(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "migrations/versions/0001_technical_baseline.py")
    )
    monkeypatch.setattr("alembic.context.is_offline_mode", lambda: False)
    connection = Mock()
    connection.scalar.return_value = False
    monkeypatch.setattr("alembic.op.get_bind", lambda: connection)
    with pytest.raises(CommandError, match="PostGIS prerequisite missing"):
        revision["upgrade"]()
