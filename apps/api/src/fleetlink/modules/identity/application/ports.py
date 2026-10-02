"""Typed Identity persistence boundary; the caller owns the transaction."""

from typing import Protocol
from uuid import UUID

from fleetlink.modules.identity.domain.user import User


class UserNotFound(LookupError):
    """A save targets an identity that does not exist."""


class IdentityConflict(RuntimeError):
    """A snapshot is stale or immutable creation metadata was changed."""


class UserRepository(Protocol):
    async def add(self, user: User) -> None:
        """Insert a version-zero identity and roles; duplicate IDs fail, never upsert."""
        ...

    async def get(self, user_id: UUID) -> User | None:
        """Return a detached consistent snapshot, or None if missing."""
        ...

    async def save(self, user: User) -> User:
        """Replace status/roles if version matches; return the incremented snapshot.

        Missing identities raise UserNotFound; stale snapshots raise IdentityConflict.
        No method commits. Let failures escape the caller's transaction for rollback.
        """
        ...
