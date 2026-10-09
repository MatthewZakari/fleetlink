"""Native request composition with an explicit pre-response transaction boundary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, TypeVar

from anyio import CancelScope
from fastapi import Depends

from fleetlink.core.dependencies import get_database
from fleetlink.infrastructure.database import Database
from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_authentication import (
    ProvisionalRefresh,
    ProvisionalRefreshReuse,
    authenticate_refresh,
)
from fleetlink.modules.identity.application.refresh_ports import RefreshTokenRepository
from fleetlink.modules.identity.application.session_ports import AuthenticationSessionRepository
from fleetlink.modules.identity.infrastructure.refresh_repository import (
    SqlAlchemyRefreshTokenRepository,
)
from fleetlink.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from fleetlink.modules.identity.infrastructure.session_repository import (
    SqlAlchemyAuthenticationSessionRepository,
)

Result = TypeVar("Result")


@dataclass(frozen=True, slots=True, repr=False)
class IdentityServices:
    """Ports bound to one session; valid only inside the operation callback."""

    users: UserRepository
    sessions: AuthenticationSessionRepository
    tokens: RefreshTokenRepository

    async def authenticate_refresh(
        self, presented: object, *, at: datetime
    ) -> ProvisionalRefresh | ProvisionalRefreshReuse:
        return await authenticate_refresh(
            presented, at=at, users=self.users, sessions=self.sessions, tokens=self.tokens
        )


@dataclass(slots=True, repr=False)
class IdentityOperation:
    """One atomic operation per request, with no result released before commit.

    The callback must propagate failures and must not emit responses, export
    credentials, retain ports, spawn tasks or commit independently. Reuse outcomes
    return normally so revocation commits; translate denial after execute returns.
    No retries or generic cross-context Unit of Work are provided.
    """

    _database: Database = field(repr=False)
    _used: bool = field(default=False, init=False)

    async def execute(self, operation: Callable[[IdentityServices], Awaitable[Result]]) -> Result:
        if self._used:
            raise RuntimeError("Identity operation already used")
        self._used = True
        async with self._database.session() as session:
            await session.begin()
            try:
                services = IdentityServices(
                    SqlAlchemyUserRepository(session),
                    SqlAlchemyAuthenticationSessionRepository(session),
                    SqlAlchemyRefreshTokenRepository(session),
                )
                result = await operation(services)
                await session.commit()
            except BaseException:
                # Cancellation must also release transactional locks and writes.
                with CancelScope(shield=True):
                    await session.rollback()
                raise
        # Session cleanup and Database's safe-error boundary finish before export.
        return result


def get_identity_operation(
    database: Annotated[Database, Depends(get_database)],
) -> IdentityOperation:
    """FastAPI caches this per request; no connection is acquired during resolution."""
    return IdentityOperation(database)


IdentityOperationDependency = Annotated[IdentityOperation, Depends(get_identity_operation)]
