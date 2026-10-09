"""Refresh acceptance/reuse orchestration within one caller-owned transaction."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshTokenNotFound,
    RefreshTokenRepository,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    RefreshCredential,
    generate_refresh_credential,
    parse_refresh_credential,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
    SessionNotFound,
)
from fleetlink.modules.identity.domain.auth_session import SessionStatus
from fleetlink.modules.identity.domain.refresh_token import (
    RefreshTokenRecord,
    RefreshTokenStatus,
    require_rotation_session,
)
from fleetlink.modules.identity.domain.user import AccountStatus


class RefreshPossessionFailed(RuntimeError):
    """Candidate lookup succeeded but the presented secret did not prove possession."""


class RefreshAccountUnavailable(RuntimeError):
    """The owning account is absent, inconsistent or not active; not authorization."""


@dataclass(frozen=True, slots=True, eq=False)
class ProvisionalRefresh:
    """Discard on rollback/commit failure; issue only AFTER the caller commits.

    No automatic serialization, diagnostic capture or early transport is permitted.
    The service cannot observe commit and does not claim issuance on return.
    """

    _credential: RefreshCredential = field(repr=False)

    def reveal(self) -> str:
        """Explicit sensitive export for issuance only after successful caller commit."""
        return self._credential.reveal()


@dataclass(frozen=True, slots=True)
class ProvisionalRefreshReuse:
    """Refresh denied; session revocation becomes effective only on caller commit.

    Return normally through the transaction to commit the response. Do not convert
    this outcome into an exception inside the transaction and undo revocation.
    There is deliberately no replacement credential on this outcome.
    """


async def authenticate_refresh(
    presented: object,
    *,
    at: datetime,
    tokens: RefreshTokenRepository,
    sessions: AuthenticationSessionRepository,
    users: UserRepository,
) -> ProvisionalRefresh | ProvisionalRefreshReuse:
    """Compose ports bound to the SAME caller-owned transaction.

    Let every exception escape that transaction, then the Database.session error
    boundary; never catch/commit partial work or automatically retry conflicts.
    Both return types are provisional until commit. Internal error distinctions
    are not a public/HTTP mapping and must not become enumeration responses.
    """
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError("Evaluation time must be timezone aware")
    at = at.astimezone(UTC)
    candidate = parse_refresh_credential(presented)
    token = await tokens.get(candidate.candidate_id)
    if token is None:
        raise RefreshTokenNotFound("Refresh token does not exist")
    # Security boundary: UUID lookup alone must NEVER trigger replay revocation.
    if not verify_refresh_credential(presented, token):
        raise RefreshPossessionFailed("Refresh credential possession failed")
    if token.status is RefreshTokenStatus.CURRENT:
        token.require_current(at)
    session = await sessions.get(token.session_id)
    if session is None:
        raise SessionNotFound("Session does not exist")
    if session.id != token.session_id:
        raise SessionConflict("Session ownership is inconsistent")
    if token.status is RefreshTokenStatus.CONSUMED:
        # Expiry/account state cannot erase confirmed consumed-credential evidence.
        # Already-revoked families need no further write or version increment.
        if session.status is SessionStatus.ACTIVE:
            await sessions.save(session.revoke())
        return ProvisionalRefreshReuse()
    require_rotation_session(token, session, at=at)
    user = await users.get(session.user_id)
    if user is None or user.id != session.user_id or user.status is not AccountStatus.ACTIVE:
        raise RefreshAccountUnavailable("Account is unavailable for refresh")
    issued = generate_refresh_credential()
    replacement = RefreshTokenRecord(
        id=issued.credential.candidate_id,
        session_id=session.id,
        verifier=issued.verifier,
        created_at=at,
        # FL-013 absolute, non-sliding expiry: never extend to the session expiry.
        expires_at=token.expires_at,
    )
    await tokens.rotate(token, replacement, session, at=at)
    return ProvisionalRefresh(issued.credential)
