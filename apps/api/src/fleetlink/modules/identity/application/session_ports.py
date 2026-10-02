"""Session persistence boundary; callers own transactions and retries."""

from typing import Protocol
from uuid import UUID

from fleetlink.modules.identity.domain.auth_session import AuthenticationSession


class SessionNotFound(LookupError):
    """A save targets an absent session."""


class SessionConflict(RuntimeError):
    """Stale state, changed immutable metadata or attempted reactivation."""


class AuthenticationSessionRepository(Protocol):
    async def add(self, session: AuthenticationSession) -> None:
        """Insert version zero; duplicate IDs/families and absent users fail.

        Database constraint failures use the existing sanitized Database boundary.
        Let all failures escape the caller's transaction to roll back atomically.
        """
        ...

    async def get(self, session_id: UUID) -> AuthenticationSession | None:
        """Return a detached immutable snapshot, or None."""
        ...

    async def get_by_family(self, family_id: UUID) -> AuthenticationSession | None:
        """Return the one stable session for a lineage, including revoked state."""
        ...

    async def save(self, session: AuthenticationSession) -> AuthenticationSession:
        """Conditionally save lifecycle and increment the persistence version.

        Missing sessions raise SessionNotFound; conflicting snapshots raise
        SessionConflict. Never reactivate. The returned state is provisional until
        caller commit. No operation begins, commits or rolls back a transaction.
        """
        ...
