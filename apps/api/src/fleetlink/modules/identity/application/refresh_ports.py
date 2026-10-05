"""Caller-owned atomic rotation; possession verification is outside this port."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord


class RefreshTokenNotFound(LookupError):
    """A conditional rotation targets an absent record."""


class RefreshTokenConflict(RuntimeError):
    """Stale or altered immutable evidence; reload, never automatically retry acceptance."""


@dataclass(frozen=True, slots=True)
class RefreshRotation:
    session: AuthenticationSession
    consumed: RefreshTokenRecord
    replacement: RefreshTokenRecord


class RefreshTokenRepository(Protocol):
    async def add(self, token: RefreshTokenRecord) -> None:
        """Insert an initial CURRENT/version-zero record; constraints reject duplicates."""
        ...

    async def get(self, token_id: UUID) -> RefreshTokenRecord | None:
        """Detached snapshot or None. This lookup does not verify possession."""
        ...

    async def rotate(
        self,
        token: RefreshTokenRecord,
        replacement: RefreshTokenRecord,
        session: AuthenticationSession,
        *,
        at: datetime,
    ) -> RefreshRotation:
        """Conditional mutation instead of unrestricted save; advance session and token.

        Caller must verify possession before use, and let EVERY error escape its
        transaction. No begin, commit, rollback or close occurs here. Results are
        provisional until caller commit. Reuse/expiry are explicit domain errors;
        missing/conflicting tokens and session CAS failures remain distinguishable.
        """
        ...
