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

    async def get_for_authentication(self, user_id: UUID) -> User | None:
        """Read current eligibility under transaction-scoped account coordination.

        Requires the caller's transaction. Block status writers until it completes;
        return a detached snapshot (or None), without changing roles or version.
        Acquire before session/token mutations; do not upgrade this read into a user
        write or release it via a savepoint. UUID lookup is not authentication proof.
        PostgreSQL composition uses READ COMMITTED; never retry failures implicitly.
        """
        ...

    async def save(self, user: User) -> User:
        """Replace status/roles if version matches; return the incremented snapshot.

        Missing identities raise UserNotFound; stale snapshots raise IdentityConflict.
        No method commits. Let failures escape the caller's transaction for rollback.
        """
        ...
