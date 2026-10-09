"""FL-013 orchestration contracts; synthetic credentials stay in memory."""

import ast
import asyncio
import logging
import secrets
import traceback
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

from fleetlink.modules.identity.application import refresh_authentication as service
from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshTokenConflict,
    RefreshTokenNotFound,
    RefreshTokenRepository,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    InvalidRefreshCredential,
    RefreshCredential,
    RefreshCredentialGenerationError,
    derive_refresh_verifier,
    parse_refresh_credential,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
    SessionNotFound,
)
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import (
    InvalidRefreshRotation,
    RefreshSessionUnavailable,
    RefreshTokenExpired,
    RefreshTokenRecord,
)
from fleetlink.modules.identity.domain.user import AccountStatus, PlatformRole, User

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
AT = CREATED + timedelta(minutes=1)
USER = User(UUID(int=1300), CREATED, roles=frozenset(PlatformRole))
SESSION = AuthenticationSession(
    UUID(int=1301), USER.id, UUID(int=1302), CREATED, CREATED + timedelta(days=1)
)


@dataclass(repr=False)
class Harness:
    credential: RefreshCredential = field(
        default_factory=lambda: RefreshCredential(UUID(int=1303), bytes(range(32)))
    )
    tokens: Mock = field(default_factory=lambda: Mock(spec=RefreshTokenRepository))
    sessions: Mock = field(default_factory=lambda: Mock(spec=AuthenticationSessionRepository))
    users: Mock = field(default_factory=lambda: Mock(spec=UserRepository))

    def __post_init__(self) -> None:
        self.token = RefreshTokenRecord(
            self.credential.candidate_id,
            SESSION.id,
            derive_refresh_verifier(self.credential),
            CREATED,
            CREATED + timedelta(hours=1),
        )
        self.tokens.get.return_value = self.token
        self.sessions.get.return_value = SESSION
        self.users.get.return_value = USER

    def run(
        self, presented: object = None, *, at: datetime = AT
    ) -> service.ProvisionalRefresh | service.ProvisionalRefreshReuse:
        return asyncio.run(
            service.authenticate_refresh(
                self.credential.reveal() if presented is None else presented,
                at=at,
                tokens=self.tokens,
                sessions=self.sessions,
                users=self.users,
            )
        )

    def assert_no_mutation(self) -> None:
        for method in (
            self.tokens.add,
            self.tokens.rotate,
            self.sessions.save,
            self.sessions.add,
            self.users.add,
            self.users.save,
        ):
            method.assert_not_called()


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_current_active_rotates_with_protocol_evidence_and_absolute_expiry(
    harness: Harness,
) -> None:
    result = harness.run(at=AT.astimezone(timezone(timedelta(hours=5))))
    assert isinstance(result, service.ProvisionalRefresh)
    harness.tokens.get.assert_awaited_once_with(harness.credential.candidate_id)
    harness.sessions.get.assert_awaited_once_with(SESSION.id)
    harness.users.get.assert_awaited_once_with(USER.id)
    harness.tokens.rotate.assert_awaited_once()
    old, replacement, session = harness.tokens.rotate.call_args.args
    assert old == harness.token and session == SESSION
    assert isinstance(replacement, RefreshTokenRecord)
    assert replacement.created_at == AT and replacement.created_at.tzinfo is UTC
    assert replacement.expires_at == harness.token.expires_at < SESSION.expires_at
    assert replacement.session_id == SESSION.id and replacement.id != old.id
    assert replacement.verifier == derive_refresh_verifier(
        parse_refresh_credential(result.reveal())
    )
    assert verify_refresh_credential(result.reveal(), replacement)
    assert bool(result.reveal().encode() != replacement.verifier.value)
    assert set(result.__slots__) == {"_credential"}
    assert not hasattr(replacement, "credential")
    diagnostics = repr(result) + str(result) + repr(replacement)
    assert bool(result.reveal() not in diagnostics)
    assert bool(result.reveal().rsplit(".", 1)[1] not in diagnostics)
    harness.sessions.save.assert_not_called()
    harness.users.save.assert_not_called()
    harness.tokens.add.assert_not_called()


@pytest.mark.parametrize("case", ["malformed", "unsupported", "uuid_only", "empty", "bytes"])
def test_invalid_input_never_reaches_repositories(harness: Harness, case: str) -> None:
    presented = {
        "malformed": "invalid",
        "unsupported": harness.credential.reveal().replace("flrt1", "flrt2", 1),
        "uuid_only": str(harness.token.id),
        "empty": "",
        "bytes": harness.credential.reveal().encode(),
    }[case]
    with pytest.raises(InvalidRefreshCredential, match="^Invalid refresh credential$"):
        harness.run(presented)
    harness.tokens.get.assert_not_called()
    harness.assert_no_mutation()


def test_unknown_candidate(harness: Harness) -> None:
    harness.tokens.get.return_value = None
    with pytest.raises(RefreshTokenNotFound, match="^Refresh token does not exist$"):
        harness.run()
    harness.sessions.get.assert_not_called()
    harness.assert_no_mutation()


@pytest.mark.parametrize("consumed", [False, True])
def test_wrong_secret_cannot_revoke_even_consumed_evidence(
    harness: Harness, consumed: bool
) -> None:
    if consumed:
        harness.tokens.get.return_value = harness.token.consume(UUID(int=1304), AT)
    wrong = RefreshCredential(harness.token.id, bytes(reversed(range(32))))
    with pytest.raises(
        service.RefreshPossessionFailed, match="^Refresh credential possession failed$"
    ):
        harness.run(wrong.reveal())
    harness.sessions.get.assert_not_called()
    harness.assert_no_mutation()


def test_mismatched_candidate_record_cannot_authenticate(harness: Harness) -> None:
    harness.tokens.get.return_value = replace(harness.token, id=UUID(int=1399))
    with pytest.raises(service.RefreshPossessionFailed):
        harness.run()
    harness.assert_no_mutation()


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(microseconds=1)])
def test_expired_current_rejected_before_generation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, delta: timedelta
) -> None:
    generate = Mock(side_effect=AssertionError("Unexpected generation"))
    monkeypatch.setattr(service, "generate_refresh_credential", generate)
    with pytest.raises(RefreshTokenExpired, match="^Refresh token has expired$"):
        harness.run(at=harness.token.expires_at + delta)
    generate.assert_not_called()
    harness.sessions.get.assert_not_called()
    harness.assert_no_mutation()


def test_last_instant_before_absolute_expiry_can_rotate(harness: Harness) -> None:
    at = harness.token.expires_at - timedelta(microseconds=1)
    assert isinstance(harness.run(at=at), service.ProvisionalRefresh)
    replacement = harness.tokens.rotate.call_args.args[1]
    assert replacement.created_at == at and replacement.expires_at == harness.token.expires_at


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "revoked",
        "expired",
        "future",
        "wrong_owner",
        "token_before_session",
        "token_after_session",
    ],
)
def test_session_unavailable_or_inconsistent_before_generation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    cases: dict[str, tuple[AuthenticationSession | None, type[Exception]]] = {
        "missing": (None, SessionNotFound),
        "revoked": (SESSION.revoke(), RefreshSessionUnavailable),
        "expired": (replace(SESSION, expires_at=AT), RefreshSessionUnavailable),
        "future": (
            replace(SESSION, created_at=AT + timedelta(seconds=1)),
            RefreshSessionUnavailable,
        ),
        "wrong_owner": (replace(SESSION, id=UUID(int=1399)), SessionConflict),
        "token_before_session": (replace(SESSION, created_at=AT), InvalidRefreshRotation),
        "token_after_session": (
            replace(SESSION, expires_at=AT + timedelta(seconds=1)),
            InvalidRefreshRotation,
        ),
    }
    harness.sessions.get.return_value, error = cases[case]
    generate = Mock(side_effect=AssertionError("Unexpected generation"))
    monkeypatch.setattr(service, "generate_refresh_credential", generate)
    with pytest.raises(error):
        harness.run()
    generate.assert_not_called()
    harness.assert_no_mutation()


@pytest.mark.parametrize(
    "user",
    [
        None,
        USER.with_status(AccountStatus.SUSPENDED),
        USER.with_status(AccountStatus.DISABLED),
        replace(USER, id=UUID(int=1399)),
    ],
)
def test_account_must_be_present_owned_and_active(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, user: User | None
) -> None:
    harness.users.get.return_value = user
    generate = Mock(side_effect=AssertionError("Unexpected generation"))
    monkeypatch.setattr(service, "generate_refresh_credential", generate)
    with pytest.raises(
        service.RefreshAccountUnavailable, match="^Account is unavailable for refresh$"
    ):
        harness.run()
    generate.assert_not_called()
    harness.assert_no_mutation()


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_confirmed_reuse_is_only_session_revocation_and_never_refresh(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, expired: bool, revoked: bool
) -> None:
    consumed = harness.token.consume(UUID(int=1304), AT)
    harness.tokens.get.return_value = consumed
    harness.sessions.get.return_value = SESSION.revoke() if revoked else SESSION
    generate = Mock(side_effect=AssertionError("Unexpected generation"))
    monkeypatch.setattr(service, "generate_refresh_credential", generate)
    result = harness.run(at=SESSION.expires_at if expired else AT)
    assert type(result) is service.ProvisionalRefreshReuse
    assert not hasattr(result, "reveal") and not hasattr(result, "_credential")
    if revoked:
        harness.assert_no_mutation()
    else:
        harness.sessions.save.assert_awaited_once_with(SESSION.revoke())
    harness.sessions.get_by_family.assert_not_called()
    harness.tokens.rotate.assert_not_called()
    harness.tokens.add.assert_not_called()
    assert harness.users.mock_calls == []
    generate.assert_not_called()
    assert harness.tokens.get.return_value == consumed


def test_repeated_confirmed_reuse_preserves_revoked_version_and_other_state(
    harness: Harness,
) -> None:
    consumed = harness.token.consume(UUID(int=1304), AT)
    harness.tokens.get.return_value = consumed
    other = replace(SESSION, id=UUID(int=1311), family_id=UUID(int=1312))
    persisted = {SESSION.id: SESSION, other.id: other}

    async def get(session_id: UUID) -> AuthenticationSession:
        return persisted[session_id]

    async def save(snapshot: AuthenticationSession) -> AuthenticationSession:
        saved = replace(snapshot, version=snapshot.version + 1)
        persisted[snapshot.id] = saved
        return saved

    harness.sessions.get.side_effect = get
    harness.sessions.save.side_effect = save
    assert type(harness.run()) is service.ProvisionalRefreshReuse
    harness.sessions.save.assert_awaited_once_with(SESSION.revoke())
    revoked = persisted[SESSION.id]
    assert revoked == replace(SESSION.revoke(), version=1)
    harness.sessions.save.reset_mock()
    for _ in range(3):
        assert type(harness.run()) is service.ProvisionalRefreshReuse
        harness.assert_no_mutation()
        assert persisted == {SESSION.id: revoked, other.id: other}
        assert harness.tokens.get.return_value == consumed
        assert harness.users.mock_calls == []


def test_generation_failure_has_no_mutations(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(size: int) -> bytes:
        raise OSError("FAKE-ENTROPY-ERROR")

    monkeypatch.setattr(secrets, "token_bytes", fail)
    with pytest.raises(RefreshCredentialGenerationError) as caught:
        harness.run()
    assert str(caught.value) == "Refresh credential generation failed"
    assert "FAKE-ENTROPY" not in "".join(traceback.format_exception(caught.value))
    harness.assert_no_mutation()


@pytest.mark.parametrize("error", [SessionConflict, RefreshTokenConflict])
def test_rotation_conflict_propagates_without_retry(
    harness: Harness, error: type[Exception]
) -> None:
    harness.tokens.rotate.side_effect = error("Snapshot conflicts with persisted state")
    with pytest.raises(error, match="^Snapshot conflicts with persisted state$"):
        harness.run()
    harness.tokens.rotate.assert_awaited_once()
    harness.tokens.get.assert_awaited_once()
    harness.sessions.save.assert_not_called()


def test_replay_conflict_propagates_without_retry(harness: Harness) -> None:
    harness.tokens.get.return_value = harness.token.consume(UUID(int=1304), AT)
    harness.sessions.save.side_effect = SessionConflict(
        "Session snapshot conflicts with persisted state"
    )
    with pytest.raises(SessionConflict):
        harness.run()
    harness.sessions.save.assert_awaited_once()
    harness.tokens.rotate.assert_not_called()


@pytest.mark.parametrize("at", [CREATED - timedelta(seconds=1), datetime(2026, 1, 1)])
def test_invalid_operation_time_has_no_mutation(harness: Harness, at: datetime) -> None:
    with pytest.raises(ValueError):
        harness.run(at=at)
    harness.assert_no_mutation()


def test_service_has_no_diagnostics(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="fleetlink"):
        harness.run()
        harness.tokens.get.return_value = harness.token.consume(UUID(int=1304), AT)
        harness.run()
        with pytest.raises(service.RefreshPossessionFailed):
            harness.run(RefreshCredential(harness.token.id, bytes(32)).reveal())
    assert caplog.records == []
    tree = ast.parse(Path(service.__file__).read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(
        name is not None and not any(part in name for part in ("logging", "telemetry", "secrets"))
        for name in imports
    )
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
