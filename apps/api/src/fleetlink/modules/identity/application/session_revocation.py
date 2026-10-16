"""Possession authorizes termination of only the evidence-owned stable lineage."""

from dataclasses import dataclass

from fleetlink.modules.identity.application.refresh_authentication import RefreshPossessionFailed
from fleetlink.modules.identity.application.refresh_ports import (
    RefreshTokenNotFound,
    RefreshTokenRepository,
)
from fleetlink.modules.identity.application.refresh_protocol import (
    parse_refresh_credential,
    verify_refresh_credential,
)
from fleetlink.modules.identity.application.session_ports import (
    AuthenticationSessionRepository,
    SessionConflict,
    SessionNotFound,
)
from fleetlink.modules.identity.domain.auth_session import SessionStatus


@dataclass(frozen=True, slots=True)
class ProvisionalSessionRevocation:
    """Non-sensitive completion; durable only after caller commit AND cleanup.

    No distinction between newly and already revoked, and no credential export.
    Discard on failure, cancellation or uncertain completion.
    """


async def revoke_session_by_refresh_credential(
    presented: object,
    *,
    tokens: RefreshTokenRepository,
    sessions: AuthenticationSessionRepository,
) -> ProvisionalSessionRevocation:
    """Use ports in the SAME caller transaction; propagate every failure, never retry.

    CURRENT/CONSUMED and expired evidence can terminate only their own lineage.
    Account eligibility and time do not restrict this reduction of capability.
    Session-only CAS must never be followed by account locking in the callback.
    IdentityOperation.execute supplies the existing commit/cleanup boundary.
    """
    candidate = parse_refresh_credential(presented)
    token = await tokens.get(candidate.candidate_id)
    if token is None:
        raise RefreshTokenNotFound("Refresh token does not exist")
    if not verify_refresh_credential(presented, token):
        raise RefreshPossessionFailed("Refresh credential possession failed")
    session = await sessions.get(token.session_id)
    if session is None:
        raise SessionNotFound("Session does not exist")
    if session.id != token.session_id:
        raise SessionConflict("Session ownership is inconsistent")
    if session.status is SessionStatus.ACTIVE:
        await sessions.save(session.revoke())
    return ProvisionalSessionRevocation()
