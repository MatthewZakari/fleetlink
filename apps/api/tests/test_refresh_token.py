"""Deterministic refresh evidence and offline migration contracts."""

import io
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import (
    InvalidRefreshRotation,
    RefreshSessionUnavailable,
    RefreshTokenExpired,
    RefreshTokenRecord,
    RefreshTokenReuse,
    RefreshTokenStatus,
    RefreshVerifier,
    prepare_rotation,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN = RefreshTokenRecord(
    UUID(int=11),
    UUID(int=10),
    RefreshVerifier(b"test-digest"),
    CREATED,
    CREATED + timedelta(hours=1),
)
NEXT = UUID(int=12)
AT = CREATED + timedelta(minutes=1)


def test_immutable_lifecycle_and_sensitive_representation() -> None:
    assert TOKEN.status is RefreshTokenStatus.CURRENT and TOKEN.version == 0
    assert TOKEN == replace(TOKEN)

    def mutate(target: object, name: str, value: object) -> None:
        setattr(target, name, value)

    with pytest.raises(FrozenInstanceError):
        mutate(TOKEN, "status", RefreshTokenStatus.CONSUMED)
    with pytest.raises(FrozenInstanceError):
        mutate(TOKEN.verifier, "value", b"changed")
    assert "test-digest" not in repr(TOKEN) + repr(TOKEN.verifier) + str(TOKEN.verifier)
    TOKEN.require_current(AT)
    consumed = TOKEN.consume(NEXT, AT)
    assert consumed.status is RefreshTokenStatus.CONSUMED
    assert consumed.replaced_by_id == NEXT and consumed.consumed_at == AT
    assert consumed.version == 0 and TOKEN.status is RefreshTokenStatus.CURRENT
    for at in (AT, TOKEN.expires_at + timedelta(days=1)):
        with pytest.raises(RefreshTokenReuse):
            consumed.require_current(at)
        with pytest.raises(RefreshTokenReuse):
            consumed.consume(NEXT, at)


def test_expiry_and_time_normalization() -> None:
    assert not TOKEN.is_expired(TOKEN.expires_at - timedelta(microseconds=1))
    for at in (TOKEN.expires_at, TOKEN.expires_at + timedelta(seconds=1)):
        assert TOKEN.is_expired(at)
        with pytest.raises(RefreshTokenExpired):
            TOKEN.consume(NEXT, at)
    with pytest.raises(ValueError):
        TOKEN.require_current(CREATED - timedelta(seconds=1))
    for operation in (TOKEN.is_expired, TOKEN.require_current):
        with pytest.raises(ValueError, match="timezone"):
            operation(datetime(2026, 1, 1))
    offset = timezone(timedelta(hours=5))
    normalized = replace(TOKEN, created_at=CREATED.astimezone(offset)).consume(
        NEXT, AT.astimezone(offset)
    )
    assert normalized.created_at.tzinfo is UTC
    assert normalized.consumed_at is not None and normalized.consumed_at.tzinfo is UTC


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "invalid"),
        ("session_id", None),
        ("verifier", b"test"),
        ("created_at", datetime(2026, 1, 1)),
        ("expires_at", None),
        ("expires_at", CREATED),
        ("expires_at", CREATED - timedelta(seconds=1)),
        ("status", "current"),
        ("status", None),
        ("version", -1),
        ("version", True),
        ("version", 1.5),
        ("consumed_at", AT),
        ("replaced_by_id", NEXT),
        ("status", RefreshTokenStatus.CONSUMED),
    ],
)
def test_invalid_current(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(TOKEN, **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("replaced_by_id", TOKEN.id),
        ("replaced_by_id", None),
        ("replaced_by_id", "invalid"),
        ("consumed_at", None),
        ("consumed_at", datetime(2026, 1, 1)),
        ("consumed_at", CREATED - timedelta(seconds=1)),
        ("consumed_at", TOKEN.expires_at),
        ("status", RefreshTokenStatus.CURRENT),
    ],
)
def test_invalid_consumed(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(TOKEN.consume(NEXT, AT), **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [b"", b"x" * 513, "text", None, bytearray(b"test")])
def test_verifier_rejects_invalid_material_without_echo(value: object) -> None:
    with pytest.raises(ValueError, match="between 1 and 512 bytes") as caught:
        RefreshVerifier(value)  # type: ignore[arg-type]
    assert repr(value) not in str(caught.value)


def test_verifier_bounds_and_self_replacement() -> None:
    for size in (1, 512):
        assert len(RefreshVerifier(b"x" * size).value) == size
    with pytest.raises(ValueError, match="different UUID"):
        TOKEN.consume(TOKEN.id, AT)


def test_offline_refresh_migration() -> None:
    output = io.StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "0003_auth_session_foundation:head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE identity_refresh_tokens" in sql
    assert "BYTEA" in sql and "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "WHERE status = 'current'" in sql and "ON DELETE RESTRICT" in sql
    assert "CREATE TABLE identity_auth_sessions" not in sql
    output.seek(0)
    output.truncate()
    command.downgrade(config, "head:0003_auth_session_foundation", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE identity_refresh_tokens" in sql
    assert "DROP TABLE identity_auth_sessions" not in sql and "DROP EXTENSION" not in sql


SESSION = AuthenticationSession(
    TOKEN.session_id, UUID(int=9), UUID(int=13), CREATED, TOKEN.expires_at
)
REPLACEMENT = replace(TOKEN, id=NEXT, created_at=AT)


def test_rotation_plan_is_pure_and_checks_session_availability() -> None:
    assert prepare_rotation(TOKEN, REPLACEMENT, SESSION, at=AT) == TOKEN.consume(NEXT, AT)
    for lineage in (
        SESSION.revoke(),
        replace(SESSION, expires_at=AT),
        replace(SESSION, created_at=AT + timedelta(seconds=1)),
    ):
        with pytest.raises(RefreshSessionUnavailable):
            prepare_rotation(TOKEN, REPLACEMENT, lineage, at=AT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", UUID(int=99)),
        ("version", 1),
        ("created_at", CREATED),
        ("expires_at", TOKEN.expires_at + timedelta(seconds=1)),
    ],
)
def test_rotation_replacement_invariants(field: str, value: object) -> None:
    with pytest.raises(InvalidRefreshRotation):
        prepare_rotation(
            TOKEN,
            replace(REPLACEMENT, **{field: value}),  # type: ignore[arg-type]
            SESSION,
            at=AT,
        )


def test_rotation_rejects_consumed_replacement_and_wrong_old_lifetime() -> None:
    with pytest.raises(InvalidRefreshRotation):
        prepare_rotation(TOKEN, REPLACEMENT.consume(UUID(int=14), AT), SESSION, at=AT)
    for token in (
        replace(TOKEN, created_at=CREATED - timedelta(seconds=1)),
        replace(TOKEN, expires_at=TOKEN.expires_at + timedelta(seconds=1)),
        replace(TOKEN, session_id=UUID(int=99)),
    ):
        with pytest.raises(InvalidRefreshRotation):
            prepare_rotation(token, REPLACEMENT, SESSION, at=AT)
