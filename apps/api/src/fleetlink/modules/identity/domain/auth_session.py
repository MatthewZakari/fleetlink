"""Immutable session lineage; possession of an identifier proves no authentication."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class SessionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class AuthenticationSession:
    """Stable lineage snapshot, not a bearer token or authorization decision."""

    id: UUID
    user_id: UUID
    family_id: UUID
    created_at: datetime
    expires_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE
    version: int = 0

    def __post_init__(self) -> None:
        for value in (self.id, self.user_id, self.family_id):
            if not isinstance(value, UUID):
                raise ValueError("Session identifiers must be UUIDs")
        for name in ("created_at", "expires_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("Session timestamps must be timezone aware")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.expires_at <= self.created_at:
            raise ValueError("Session expiry must follow creation")
        if not isinstance(self.status, SessionStatus):
            raise ValueError("Invalid session status")
        if type(self.version) is not int or self.version < 0:
            raise ValueError("Version must be a nonnegative integer")

    def revoke(self) -> "AuthenticationSession":
        return replace(self, status=SessionStatus.REVOKED)

    def is_expired(self, at: datetime) -> bool:
        if not isinstance(at, datetime) or at.utcoffset() is None:
            raise ValueError("Evaluation time must be timezone aware")
        return at >= self.expires_at
