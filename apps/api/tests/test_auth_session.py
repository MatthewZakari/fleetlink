"""Pure session snapshots and offline schema ownership."""

import io
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
SESSION = AuthenticationSession(
    UUID(int=10), UUID(int=9), UUID(int=11), CREATED, CREATED + timedelta(days=1)
)


def test_snapshot_equality_immutability_revocation_and_expiry() -> None:
    assert SESSION == replace(SESSION)
    assert SESSION.status is SessionStatus.ACTIVE and SESSION.version == 0

    def mutate(target: object, field: str) -> None:
        setattr(target, field, SessionStatus.REVOKED)

    with pytest.raises(FrozenInstanceError):
        mutate(SESSION, "status")
    revoked = SESSION.revoke()
    assert revoked.status is SessionStatus.REVOKED
    assert revoked.revoke() == revoked
    assert revoked.version == 0
    assert SESSION.status is SessionStatus.ACTIVE
    assert not SESSION.is_expired(SESSION.expires_at - timedelta(microseconds=1))
    assert SESSION.is_expired(SESSION.expires_at)
    assert SESSION.is_expired(SESSION.expires_at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone"):
        SESSION.is_expired(datetime(2026, 1, 1))


@pytest.mark.parametrize("field", ["id", "user_id", "family_id"])
@pytest.mark.parametrize("value", ["00000000-0000-4000-8000-000000000010", None, 1])
def test_uuid_validation(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="UUID"):
        replace(SESSION, **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["created_at", "expires_at"])
@pytest.mark.parametrize("value", [datetime(2026, 1, 1), "2026-01-01", None])
def test_timestamp_validation(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="timezone"):
        replace(SESSION, **{field: value})  # type: ignore[arg-type]


def test_normalization_and_expiry_order() -> None:
    offset = timezone(timedelta(hours=5))
    session = replace(
        SESSION,
        created_at=CREATED.astimezone(offset),
        expires_at=SESSION.expires_at.astimezone(offset),
    )
    assert session == SESSION
    assert session.created_at.tzinfo is UTC and session.expires_at.tzinfo is UTC
    for expiry in (CREATED, CREATED - timedelta(seconds=1)):
        with pytest.raises(ValueError, match="expiry"):
            replace(SESSION, expires_at=expiry)


@pytest.mark.parametrize("value", ["active", "revoked", "expired", None])
def test_status_validation(value: object) -> None:
    with pytest.raises(ValueError, match="status"):
        replace(SESSION, status=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, True, 1.5, "0", None])
def test_version_validation(value: object) -> None:
    with pytest.raises(ValueError, match="Version"):
        replace(SESSION, version=value)  # type: ignore[arg-type]


def test_offline_session_migration() -> None:
    output = io.StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "0002_identity_foundation:head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE identity_auth_sessions" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "UNIQUE (family_id)" in sql
    assert "expires_at > created_at" in sql
    assert "CREATE TABLE identity_users" not in sql
    output.seek(0)
    output.truncate()
    command.downgrade(config, "head:0002_identity_foundation", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE identity_auth_sessions" in sql
    assert "DROP TABLE identity_users" not in sql
    assert "DROP EXTENSION" not in sql
