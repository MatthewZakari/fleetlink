"""Algorithm-neutral refresh evidence; identifiers alone prove no possession."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fleetlink.modules.identity.domain.auth_session import AuthenticationSession, SessionStatus


@dataclass(frozen=True, slots=True)
class RefreshVerifier:
    """Sensitive one-way material supplied by a future reviewed protocol, never raw tokens."""

    value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.value) is not bytes or not 1 <= len(self.value) <= 512:
            raise ValueError("Verifier must contain between 1 and 512 bytes")


class RefreshTokenStatus(StrEnum):
    CURRENT = "current"
    CONSUMED = "consumed"


class RefreshTokenReuse(RuntimeError):
    """Consumed evidence presented again; never an idempotent refresh success."""


class RefreshTokenExpired(RuntimeError):
    """Explicit evaluation time is at or past expiry."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Token timestamps must be timezone aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    id: UUID
    session_id: UUID
    verifier: RefreshVerifier = field(repr=False)
    created_at: datetime
    expires_at: datetime
    status: RefreshTokenStatus = RefreshTokenStatus.CURRENT
    replaced_by_id: UUID | None = None
    consumed_at: datetime | None = None
    version: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or not isinstance(self.session_id, UUID):
            raise ValueError("Token identifiers must be UUIDs")
        if not isinstance(self.verifier, RefreshVerifier):
            raise ValueError("Invalid verifier representation")
        for name in ("created_at", "expires_at"):
            object.__setattr__(self, name, _utc(getattr(self, name)))
        if self.expires_at <= self.created_at:
            raise ValueError("Token expiry must follow creation")
        if not isinstance(self.status, RefreshTokenStatus):
            raise ValueError("Invalid token status")
        if type(self.version) is not int or self.version < 0:
            raise ValueError("Version must be a nonnegative integer")
        if self.status is RefreshTokenStatus.CURRENT:
            if self.replaced_by_id is not None or self.consumed_at is not None:
                raise ValueError("Current token cannot have consumption metadata")
        else:
            if not isinstance(self.replaced_by_id, UUID) or self.replaced_by_id == self.id:
                raise ValueError("Replacement must be a different UUID")
            if self.consumed_at is None:
                raise ValueError("Consumed token requires consumption time")
            object.__setattr__(self, "consumed_at", _utc(self.consumed_at))
            if not self.created_at <= self.consumed_at < self.expires_at:
                raise ValueError("Consumption must occur during token lifetime")

    def is_expired(self, at: datetime) -> bool:
        return _utc(at) >= self.expires_at

    def require_current(self, at: datetime) -> None:
        at = _utc(at)
        # Reuse remains distinguishable even after the consumed record expires.
        if self.status is RefreshTokenStatus.CONSUMED:
            raise RefreshTokenReuse("Refresh token was already consumed")
        if self.is_expired(at):
            raise RefreshTokenExpired("Refresh token has expired")
        if at < self.created_at:
            raise ValueError("Evaluation time precedes token creation")

    def consume(self, replacement_id: UUID, at: datetime) -> "RefreshTokenRecord":
        self.require_current(at)
        return replace(
            self,
            status=RefreshTokenStatus.CONSUMED,
            replaced_by_id=replacement_id,
            consumed_at=at,
        )


class InvalidRefreshRotation(ValueError):
    """Replacement evidence is incompatible with its stable lineage."""


class RefreshSessionUnavailable(RuntimeError):
    """Session is revoked, expired or outside its lifetime."""


def prepare_rotation(
    token: RefreshTokenRecord,
    replacement: RefreshTokenRecord,
    session: AuthenticationSession,
    *,
    at: datetime,
) -> RefreshTokenRecord:
    """Validate lineage/lifetime invariants and produce provisional consumed evidence."""
    consumed = token.consume(replacement.id, at)
    require_rotation_session(token, session, at=at)
    if (
        replacement.session_id != session.id
        or replacement.status is not RefreshTokenStatus.CURRENT
        or replacement.version != 0
        or replacement.created_at != consumed.consumed_at
        or replacement.expires_at > session.expires_at
    ):
        raise InvalidRefreshRotation("Rotation snapshots are incompatible")
    return consumed


def require_rotation_session(
    token: RefreshTokenRecord,
    session: AuthenticationSession,
    *,
    at: datetime,
) -> None:
    """Shared lineage checks, also usable before generating replacement material."""
    at = _utc(at)
    if (
        session.status is not SessionStatus.ACTIVE
        or session.is_expired(at)
        or at < session.created_at
    ):
        raise RefreshSessionUnavailable("Session is unavailable for rotation")
    if (
        token.session_id != session.id
        or token.created_at < session.created_at
        or token.expires_at > session.expires_at
    ):
        raise InvalidRefreshRotation("Rotation snapshots are incompatible")
