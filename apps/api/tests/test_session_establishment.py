"""FL-015 trusted composition; synthetic sensitive material stays in memory."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from fleetlink.modules.identity.application import session_establishment as service
from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_ports import RefreshTokenRepository
from fleetlink.modules.identity.application.refresh_protocol import (
    RefreshCredentialGenerationError,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
)
from fleetlink.modules.identity.domain.auth_session import SessionStatus
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenStatus
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User

AT = datetime(2026, 1, 1, tzinfo=UTC)
USER = User(UUID(int=1500), AT - timedelta(days=1))
ASSERTION = service.TrustedAuthenticationAssertion(USER.id, AT)
POLICY = service.SessionLifetimePolicy(timedelta(hours=2), timedelta(hours=1))


class Harness:
    def __init__(self) -> None:
        self.users = Mock(spec=UserRepository)
        self.sessions = Mock(spec=AuthenticationSessionRepository)
        self.tokens = Mock(spec=RefreshTokenRepository)
        self.users.get.return_value = USER
        self.arguments: dict[str, Any] = {
            "assertion": ASSERTION,
            "session_id": UUID(int=1501),
            "family_id": UUID(int=1502),
            "created_at": AT,
            "session_expires_at": AT + POLICY.max_session_lifetime,
            "refresh_expires_at": AT + POLICY.max_refresh_lifetime,
            "lifetime_policy": POLICY,
            "users": self.users,
            "sessions": self.sessions,
            "tokens": self.tokens,
        }

    def run(self, **changes: Any) -> service.ProvisionalSessionEstablishment:
        return asyncio.run(service.establish_initial_session(**(self.arguments | changes)))

    def no_writes(self) -> None:
        for method in (
            self.users.add,
            self.users.save,
            self.sessions.add,
            self.sessions.save,
            self.tokens.add,
            self.tokens.rotate,
        ):
            method.assert_not_called()


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.mark.parametrize("roles", [frozenset(), frozenset(PlatformRole)])
def test_initial_snapshots_protocol_and_order(
    harness: Harness, roles: frozenset[PlatformRole]
) -> None:
    harness.users.get.return_value = replace(USER, roles=roles)
    order: list[str] = []
    harness.sessions.add.side_effect = lambda _: order.append("session")
    harness.tokens.add.side_effect = lambda _: order.append("token")
    result = harness.run(created_at=AT.astimezone(timezone(timedelta(hours=5))))
    session = harness.sessions.add.call_args.args[0]
    token = harness.tokens.add.call_args.args[0]
    assert order == ["session", "token"]
    assert session.user_id == USER.id and token.session_id == session.id == result.session_id
    assert session.family_id == result.family_id
    assert session.created_at == token.created_at == result.created_at == AT
    assert session.created_at.tzinfo is UTC
    assert session.status is SessionStatus.ACTIVE and session.version == 0
    assert token.status is RefreshTokenStatus.CURRENT and token.version == 0
    assert token.consumed_at is None and token.replaced_by_id is None
    assert token.expires_at == result.refresh_expires_at == AT + POLICY.max_refresh_lifetime
    assert session.expires_at == result.session_expires_at == AT + POLICY.max_session_lifetime
    # Unit-only export inspects the contract; fake repositories cannot establish durability.
    assert verify_refresh_credential(result.reveal(), token)
    assert not hasattr(token, "credential")
    harness.users.get.assert_awaited_once_with(USER.id)
    harness.users.save.assert_not_called()
    harness.sessions.save.assert_not_called()
    harness.tokens.rotate.assert_not_called()
    with pytest.raises(FrozenInstanceError):
        result.session_id = UUID(int=9)  # type: ignore[misc]


@pytest.mark.parametrize(
    "user",
    [
        None,
        replace(USER, id=UUID(int=9)),
        replace(USER, status=AccountStatus.SUSPENDED),
        replace(USER, status=AccountStatus.DISABLED),
    ],
)
def test_account_rejection_precedes_generation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, user: User | None
) -> None:
    harness.users.get.return_value = user
    generator = Mock()
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(service.SessionAccountUnavailable, match="^Account is unavailable"):
        harness.run()
    generator.assert_not_called()
    harness.no_writes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("assertion", USER.id),
        ("assertion", {"user_id": USER.id}),
        ("assertion", None),
        ("lifetime_policy", None),
        ("lifetime_policy", {"max_session_lifetime": 100}),
        ("session_id", "private-invalid"),
        ("family_id", None),
        ("created_at", datetime(2026, 1, 1)),
        ("created_at", "private-invalid"),
        ("session_expires_at", datetime(2026, 1, 2)),
        ("refresh_expires_at", None),
        ("created_at", AT - timedelta(seconds=1)),
        ("session_expires_at", AT),
        ("session_expires_at", AT - timedelta(seconds=1)),
        ("refresh_expires_at", AT),
        ("refresh_expires_at", AT - timedelta(seconds=1)),
        ("refresh_expires_at", AT + timedelta(hours=3)),
        ("session_expires_at", AT + timedelta(hours=2, microseconds=1)),
        ("refresh_expires_at", AT + timedelta(hours=1, microseconds=1)),
    ],
)
def test_invalid_inputs_before_io(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    generator = Mock()
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(ValueError) as caught:
        harness.run(**{field: value})
    assert "private-invalid" not in str(caught.value)
    harness.users.get.assert_not_called()
    generator.assert_not_called()
    harness.no_writes()


def test_authentication_before_account_creation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = Mock()
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(ValueError, match="^Authentication cannot precede"):
        harness.run(
            assertion=replace(ASSERTION, authenticated_at=USER.created_at - timedelta(seconds=1))
        )
    generator.assert_not_called()
    harness.no_writes()


@pytest.mark.parametrize("field", ["max_session_lifetime", "max_refresh_lifetime"])
@pytest.mark.parametrize(
    "value", [timedelta(0), timedelta(seconds=-1), None, True, 3600, "private-invalid"]
)
def test_policy_validation(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="^Maximum lifetimes must be positive timedeltas$"):
        replace(POLICY, **{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "private-invalid"),
        ("user_id", None),
        ("authenticated_at", datetime(2026, 1, 1)),
        ("authenticated_at", "private-invalid"),
    ],
)
def test_assertion_validation(field: str, value: Any) -> None:
    with pytest.raises(ValueError) as caught:
        replace(ASSERTION, **{field: value})
    assert "private-invalid" not in str(caught.value)


def test_immutable_assertion_policy_and_equal_expiries(harness: Harness) -> None:
    for value in (ASSERTION, POLICY):
        with pytest.raises(FrozenInstanceError):
            setattr(value, next(iter(value.__slots__)), None)
    assertion = replace(ASSERTION, authenticated_at=AT.astimezone(timezone(timedelta(hours=5))))
    assert assertion.authenticated_at.tzinfo is UTC
    result = harness.run(assertion=assertion, session_expires_at=AT + timedelta(hours=1))
    assert result.session_expires_at == result.refresh_expires_at


def test_generation_failure_no_writes(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    generator = Mock(
        side_effect=RefreshCredentialGenerationError("Refresh credential generation failed")
    )
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(RefreshCredentialGenerationError):
        harness.run()
    generator.assert_called_once_with()
    harness.no_writes()


@pytest.mark.parametrize("stage", ["user", "session", "token"])
def test_failure_propagates_without_retry(harness: Harness, stage: str) -> None:
    method = {
        "user": harness.users.get,
        "session": harness.sessions.add,
        "token": harness.tokens.add,
    }[stage]
    failure = SessionConflict("Synthetic conflict")
    method.side_effect = failure
    with pytest.raises(SessionConflict) as caught:
        harness.run()
    assert caught.value is failure
    method.assert_awaited_once()
    if stage != "token":
        harness.tokens.add.assert_not_called()
    if stage == "user":
        harness.sessions.add.assert_not_called()


def test_sensitive_representations_and_no_diagnostics(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    result = harness.run()
    token = harness.tokens.add.call_args.args[0]
    diagnostic = repr(result) + str(result) + repr(token) + repr(token.verifier) + caplog.text
    wire = result.reveal()
    for sensitive in (
        wire,
        wire.rsplit(".", 1)[1],
        repr(token.verifier.value),
        token.verifier.value.hex(),
    ):
        assert bool(sensitive not in diagnostic)
    assert not caplog.records
