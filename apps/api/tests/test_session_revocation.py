"""FL-017 proof-before-effect and non-sensitive provisional completion."""

import asyncio
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

import pytest

from fleetlink.modules.identity.application import session_revocation as service
from fleetlink.modules.identity.application.refresh_authentication import RefreshPossessionFailed
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshTokenNotFound,
    RefreshTokenRepository,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    InvalidRefreshCredential,
    RefreshCredential,
    derive_refresh_verifier,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
    SessionNotFound,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord, RefreshVerifier

AT = datetime(2026, 1, 1, tzinfo=UTC)
SESSION = AuthenticationSession(
    UUID(int=1701), UUID(int=1700), UUID(int=1702), AT, AT + timedelta(hours=2)
)


def credential() -> RefreshCredential:
    return RefreshCredential(UUID(int=1703), bytes(range(32)))


class Harness:
    def __init__(self) -> None:
        self.tokens = Mock(spec=RefreshTokenRepository)
        self.sessions = Mock(spec=AuthenticationSessionRepository)
        self.token = RefreshTokenRecord(
            credential().candidate_id,
            SESSION.id,
            derive_refresh_verifier(credential()),
            AT,
            AT + timedelta(hours=1),
        )
        self.tokens.get.return_value = self.token
        self.sessions.get.return_value = SESSION

    def run(self, presented: object) -> service.ProvisionalSessionRevocation:
        return asyncio.run(
            service.revoke_session_by_refresh_credential(
                presented, tokens=self.tokens, sessions=self.sessions
            )
        )

    def no_writes(self) -> None:
        self.sessions.save.assert_not_called()
        self.sessions.add.assert_not_called()
        self.tokens.add.assert_not_called()
        self.tokens.rotate.assert_not_called()


@pytest.mark.parametrize(
    "case", ["malformed", "unknown_version", "noncanonical", "oversized", "type"]
)
def test_invalid_syntax_never_reads(case: str) -> None:
    harness = Harness()
    wire = credential().reveal()
    presented = {
        "malformed": "invalid",
        "unknown_version": wire.replace("flrt1", "flrt2"),
        "noncanonical": wire + "=",
        "oversized": "x" * 4096,
        "type": {},
    }[case]
    with pytest.raises(InvalidRefreshCredential):
        harness.run(presented)
    assert harness.tokens.mock_calls == harness.sessions.mock_calls == []


def test_unknown_candidate_does_not_load_session() -> None:
    harness = Harness()
    harness.tokens.get.return_value = None
    with pytest.raises(RefreshTokenNotFound):
        harness.run(credential().reveal())
    assert harness.sessions.mock_calls == []
    harness.no_writes()


@pytest.mark.parametrize("case", ["secret", "substitution", "evidence_id", "verifier"])
def test_forged_possession_cannot_load_or_mutate_session(case: str) -> None:
    harness = Harness()
    presented = credential()
    if case == "secret":
        presented = RefreshCredential(presented.candidate_id, bytes(reversed(range(32))))
    elif case == "substitution":
        presented = RefreshCredential(UUID(int=1799), bytes(range(32)))
        harness.tokens.get.return_value = replace(harness.token, id=presented.candidate_id)
    elif case == "evidence_id":
        harness.tokens.get.return_value = replace(harness.token, id=UUID(int=1799))
    else:
        harness.tokens.get.return_value = replace(harness.token, verifier=RefreshVerifier(b"bad"))
    with pytest.raises(RefreshPossessionFailed):
        harness.run(presented.reveal())
    assert harness.sessions.mock_calls == []
    harness.no_writes()


@pytest.mark.parametrize("consumed", [False, True])
@pytest.mark.parametrize("expired_evidence", [False, True])
@pytest.mark.parametrize("expired_session", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_lifecycle_does_not_restrict_termination(
    consumed: bool,
    expired_evidence: bool,
    expired_session: bool,
    revoked: bool,
) -> None:
    harness = Harness()
    # Dates straddle a fixed reference; the operation deliberately has no clock input.
    reference = AT + timedelta(days=1)
    session = replace(
        SESSION, expires_at=reference + timedelta(days=-0.5 if expired_session else 2)
    )
    token = replace(
        harness.token, expires_at=reference + timedelta(days=-0.75 if expired_evidence else 1)
    )
    if consumed:
        token = token.consume(UUID(int=1704), AT + timedelta(minutes=1))
    if revoked:
        session = session.revoke()
    harness.tokens.get.return_value = token
    harness.sessions.get.return_value = session
    result = harness.run(credential().reveal())
    assert result == service.ProvisionalSessionRevocation()
    assert fields(result) == () and not hasattr(result, "reveal")
    harness.tokens.get.assert_awaited_once_with(token.id)
    harness.sessions.get.assert_awaited_once_with(token.session_id)
    if revoked:
        harness.no_writes()
    else:
        harness.sessions.save.assert_awaited_once_with(session.revoke())
    harness.tokens.add.assert_not_called()
    harness.tokens.rotate.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_session_consistency(missing: bool) -> None:
    harness = Harness()
    harness.sessions.get.return_value = None if missing else replace(SESSION, id=UUID(int=1799))
    with pytest.raises(SessionNotFound if missing else SessionConflict):
        harness.run(credential().reveal())
    harness.no_writes()


def test_order_and_conflict_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness()
    order: list[str] = []
    original = verify_refresh_credential

    def verify(presented: object, record: RefreshTokenRecord) -> bool:
        order.append("proof")
        return original(presented, record)

    async def evidence(identifier: UUID) -> RefreshTokenRecord:
        order.append("evidence")
        return harness.token

    async def session(identifier: UUID) -> AuthenticationSession:
        order.append("session")
        return SESSION

    async def conflict(snapshot: AuthenticationSession) -> AuthenticationSession:
        order.append("cas")
        raise SessionConflict("Session snapshot conflicts with persisted state")

    monkeypatch.setattr(service, "verify_refresh_credential", verify)
    harness.tokens.get.side_effect = evidence
    harness.sessions.get.side_effect = session
    harness.sessions.save.side_effect = conflict
    with pytest.raises(SessionConflict):
        harness.run(credential().reveal())
    assert order == ["evidence", "proof", "session", "cas"]
    harness.sessions.save.assert_awaited_once()


def test_repeated_revocation_and_private_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    harness = Harness()
    result = harness.run(credential().reveal())
    harness.sessions.get.return_value = SESSION.revoke()
    for _ in range(3):
        assert harness.run(credential().reveal()) == result
    harness.sessions.save.assert_awaited_once()
    assert repr(result) == "ProvisionalSessionRevocation()"
    with pytest.raises((FrozenInstanceError, TypeError)):
        result.extra = "invalid"  # type: ignore[attr-defined]
    harness.sessions.get.return_value = None
    with pytest.raises(SessionNotFound) as caught:
        harness.run(credential().reveal())
    output = caplog.text + repr(result) + str(caught.value)
    assert bool(credential().reveal() not in output)
    assert bool(str(harness.token.verifier.value) not in output)
    assert caplog.records == []
