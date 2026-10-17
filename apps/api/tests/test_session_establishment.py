"""FL-015 trusted composition; synthetic sensitive material stays in memory."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from fleetlink.modules.identity.application import session_establishment as service
from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_ports import RefreshTokenRepository
from fleetlink.modules.identity.application.refresh_protocol import (
    IssuedRefreshCredential,
    RefreshCredentialGenerationError,
    generate_refresh_credential,
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
        self.users.get_for_authentication.return_value = USER
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
    harness.users.get_for_authentication.return_value = replace(USER, roles=roles)
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
    harness.users.get_for_authentication.assert_awaited_once_with(USER.id)
    harness.users.get.assert_not_called()
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
    harness.users.get_for_authentication.return_value = user
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
    harness.users.get_for_authentication.assert_not_called()
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


def test_coordination_precedes_generation_and_writes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    generate = generate_refresh_credential

    async def read(user_id: UUID) -> User:
        order.append("account")
        return USER

    def issue() -> IssuedRefreshCredential:
        order.append("generate")
        return generate()

    harness.users.get_for_authentication.side_effect = read
    harness.sessions.add.side_effect = lambda _: order.append("session")
    harness.tokens.add.side_effect = lambda _: order.append("token")
    monkeypatch.setattr(service, "generate_refresh_credential", issue)
    harness.run()
    assert order == ["account", "generate", "session", "token"]


@pytest.mark.parametrize("stage", ["user", "session", "token"])
def test_failure_propagates_without_retry(harness: Harness, stage: str) -> None:
    method = {
        "user": harness.users.get_for_authentication,
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


FRESHNESS = service.AuthenticationFreshnessPolicy(timedelta(minutes=5))


@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=5) - timedelta(microseconds=1)])
def test_fresh_admission_delegates_once_and_preserves_result(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, age: timedelta
) -> None:
    assertion = replace(ASSERTION, authenticated_at=AT - age)
    delegate = AsyncMock(wraps=service.establish_initial_session)
    monkeypatch.setattr(service, "establish_initial_session", delegate)
    arguments = harness.arguments | {"assertion": assertion}
    result = asyncio.run(
        service.establish_fresh_initial_session(**arguments, freshness_policy=FRESHNESS)
    )
    delegate.assert_awaited_once_with(**arguments)
    assert result.session_id == arguments["session_id"]
    harness.users.get_for_authentication.assert_awaited_once_with(USER.id)
    harness.sessions.add.assert_awaited_once()
    harness.tokens.add.assert_awaited_once()


@pytest.mark.parametrize("age", [timedelta(minutes=5), timedelta(minutes=6)])
def test_stale_admission_has_no_effect_or_diagnostics(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    age: timedelta,
) -> None:
    delegate = AsyncMock()
    generator = Mock()
    monkeypatch.setattr(service, "establish_initial_session", delegate)
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(service.AuthenticationAssertionStale) as caught:
        asyncio.run(
            service.establish_fresh_initial_session(
                **(
                    harness.arguments | {"assertion": replace(ASSERTION, authenticated_at=AT - age)}
                ),
                freshness_policy=FRESHNESS,
            )
        )
    assert str(caught.value) == "Authentication assertion is stale"
    assert repr(caught.value) == "AuthenticationAssertionStale('Authentication assertion is stale')"
    delegate.assert_not_called()
    generator.assert_not_called()
    for port in (harness.users, harness.sessions, harness.tokens):
        assert not port.mock_calls
    assert not caplog.records


@pytest.mark.parametrize(
    "value", [timedelta(0), timedelta(microseconds=-1), None, True, 300, 3.0, "private-invalid"]
)
def test_freshness_policy_validation(value: Any) -> None:
    with pytest.raises(ValueError) as caught:
        service.AuthenticationFreshnessPolicy(value)
    assert str(caught.value) == "Maximum assertion age must be a positive timedelta"
    assert "private-invalid" not in repr(caught.value)


def test_freshness_policy_is_explicit_and_immutable() -> None:
    with pytest.raises(TypeError):
        service.AuthenticationFreshnessPolicy()  # type: ignore[call-arg]
    with pytest.raises(FrozenInstanceError):
        FRESHNESS.max_assertion_age = timedelta(days=1)  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"assertion": None},
        {"assertion": {"user_id": "private-invalid"}},
        {"assertion": replace(ASSERTION, authenticated_at=AT + timedelta(microseconds=1))},
        {"created_at": datetime(2026, 1, 1)},
        {"created_at": "private-invalid"},
        {"freshness_policy": None},
        {"freshness_policy": {"max_assertion_age": 300}},
    ],
)
def test_invalid_fresh_admission_never_delegates_or_accesses_ports(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    delegate = AsyncMock()
    generator = Mock()
    monkeypatch.setattr(service, "establish_initial_session", delegate)
    monkeypatch.setattr(service, "generate_refresh_credential", generator)
    with pytest.raises(ValueError) as caught:
        asyncio.run(
            service.establish_fresh_initial_session(
                **(harness.arguments | {"freshness_policy": FRESHNESS} | changes)
            )
        )
    assert "private-invalid" not in str(caught.value) + repr(caught.value)
    delegate.assert_not_called()
    generator.assert_not_called()
    for port in (harness.users, harness.sessions, harness.tokens):
        assert not port.mock_calls


def test_fresh_admission_utc_equivalence_and_unchanged_result(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    delegate = AsyncMock(return_value=expected)
    monkeypatch.setattr(service, "establish_initial_session", delegate)
    offset = timezone(timedelta(hours=-7))
    assertion = replace(ASSERTION, authenticated_at=AT.astimezone(offset))
    result = asyncio.run(
        service.establish_fresh_initial_session(
            **(harness.arguments | {"assertion": assertion, "created_at": AT.astimezone(offset)}),
            freshness_policy=FRESHNESS,
        )
    )
    assert result is expected
    delegate.assert_awaited_once_with(**(harness.arguments | {"assertion": assertion}))
    assert delegate.call_args.kwargs["created_at"].tzinfo is UTC


def test_legacy_establishment_still_accepts_old_assertion(harness: Harness) -> None:
    result = harness.run(assertion=replace(ASSERTION, authenticated_at=AT - timedelta(hours=1)))
    assert result.created_at == AT
    harness.tokens.add.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", [SessionConflict("Synthetic conflict"), RuntimeError("Synthetic failure")]
)
def test_fresh_admission_propagates_delegate_failure_without_retry(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    delegate = AsyncMock(side_effect=failure)
    monkeypatch.setattr(service, "establish_initial_session", delegate)
    with pytest.raises(type(failure)) as caught:
        asyncio.run(
            service.establish_fresh_initial_session(
                **harness.arguments,
                freshness_policy=FRESHNESS,
            )
        )
    assert caught.value is failure
    delegate.assert_awaited_once_with(**harness.arguments)
