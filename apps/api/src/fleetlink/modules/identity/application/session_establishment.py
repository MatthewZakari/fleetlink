"""Initial lineage creation for trusted callers, inside their existing transaction."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fleetlink.modules.identity.application.ports import UserRepository
from fleetlink.modules.identity.application.refresh_authentication import ProvisionalRefresh
from fleetlink.modules.identity.application.refresh_ports import RefreshTokenRepository
from fleetlink.modules.identity.application.refresh_protocol import generate_refresh_credential
from fleetlink.modules.identity.application.session_ports import AuthenticationSessionRepository
from fleetlink.modules.identity.domain.auth_session import AuthenticationSession
from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord
from fleetlink.modules.identity.domain.user import AccountStatus


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Establishment timestamps must be timezone aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TrustedAuthenticationAssertion:
    """Trusted-caller convention, NOT cryptographic proof or an unforgeable capability.

    Only trusted composition after successful authentication may construct this.
    Never construct it from a client user ID/flag or deserialize a request into it.
    This type neither authenticates the caller nor verifies assertion freshness.
    """

    user_id: UUID
    authenticated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValueError("Asserted user identifier must be a UUID")
        object.__setattr__(self, "authenticated_at", _utc(self.authenticated_at))


@dataclass(frozen=True, slots=True)
class SessionLifetimePolicy:
    """Identity-owned limits supplied by trusted composition, never client input.

    No production values/defaults are selected here. Deployment policy and assertion
    freshness require separate review before activation.
    """

    max_session_lifetime: timedelta
    max_refresh_lifetime: timedelta

    def __post_init__(self) -> None:
        for value in (self.max_session_lifetime, self.max_refresh_lifetime):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError("Maximum lifetimes must be positive timedeltas")


class SessionAccountUnavailable(RuntimeError):
    """Absent, inconsistent or ineligible account; never a public enumeration result."""


@dataclass(frozen=True, slots=True, eq=False)
class ProvisionalSessionEstablishment:
    """Sensitive transient result; discard on rollback, cancellation or uncertainty.

    Never serialize or capture internals. Repr masking is not memory protection.
    The service cannot observe commit and this wrapper cannot enforce caller conduct.
    """

    session_id: UUID
    family_id: UUID
    created_at: datetime
    session_expires_at: datetime
    refresh_expires_at: datetime
    _refresh: ProvisionalRefresh = field(repr=False)

    def reveal(self) -> str:
        """Sensitive export only after IdentityOperation.execute commits AND cleans up."""
        return self._refresh.reveal()


async def establish_initial_session(
    assertion: TrustedAuthenticationAssertion,
    *,
    session_id: UUID,
    family_id: UUID,
    created_at: datetime,
    session_expires_at: datetime,
    refresh_expires_at: datetime,
    lifetime_policy: SessionLifetimePolicy,
    users: UserRepository,
    sessions: AuthenticationSessionRepository,
    tokens: RefreshTokenRepository,
) -> ProvisionalSessionEstablishment:
    """Compose ports bound to the SAME caller-owned transaction; never retry.

    All failures must escape the transaction and sanitized Database session boundary.
    FL-014's unchanged IdentityOperation callback can supply these three ports. Do
    not export/retain the result before execute returns after commit and cleanup.
    Account coordination precedes session/token writes and lasts until transaction end.
    """
    if not isinstance(assertion, TrustedAuthenticationAssertion):
        raise ValueError("A trusted authentication assertion is required")
    if not isinstance(lifetime_policy, SessionLifetimePolicy):
        raise ValueError("A validated session lifetime policy is required")
    created_at = _utc(created_at)
    session_expires_at = _utc(session_expires_at)
    refresh_expires_at = _utc(refresh_expires_at)
    # Reuse domain UUID, time and session-expiry validation before any I/O.
    session = AuthenticationSession(
        session_id, assertion.user_id, family_id, created_at, session_expires_at
    )
    if (
        assertion.authenticated_at > created_at
        or not created_at < refresh_expires_at <= session_expires_at
        or session_expires_at - created_at > lifetime_policy.max_session_lifetime
        or refresh_expires_at - created_at > lifetime_policy.max_refresh_lifetime
    ):
        raise ValueError("Establishment chronology or lifetime is invalid")
    user = await users.get_for_authentication(assertion.user_id)
    if user is None or user.id != assertion.user_id or user.status is not AccountStatus.ACTIVE:
        raise SessionAccountUnavailable("Account is unavailable for session establishment")
    if user.created_at > assertion.authenticated_at:
        raise ValueError("Authentication cannot precede account creation")
    issued = generate_refresh_credential()
    token = RefreshTokenRecord(
        id=issued.credential.candidate_id,
        session_id=session.id,
        verifier=issued.verifier,
        created_at=created_at,
        expires_at=refresh_expires_at,
    )
    await sessions.add(session)
    await tokens.add(token)
    return ProvisionalSessionEstablishment(
        session.id,
        session.family_id,
        created_at,
        session_expires_at,
        refresh_expires_at,
        ProvisionalRefresh(issued.credential),
    )


@dataclass(frozen=True, slots=True)
class AuthenticationFreshnessPolicy:
    """Explicit trusted-composition admission limit, with no production default."""

    max_assertion_age: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.max_assertion_age, timedelta) or self.max_assertion_age <= timedelta(
            0
        ):
            raise ValueError("Maximum assertion age must be a positive timedelta")


class AuthenticationAssertionStale(RuntimeError):
    """Assertion age meets or exceeds the explicit admission limit; not a public error."""


async def establish_fresh_initial_session(
    assertion: TrustedAuthenticationAssertion,
    *,
    freshness_policy: AuthenticationFreshnessPolicy,
    session_id: UUID,
    family_id: UUID,
    created_at: datetime,
    session_expires_at: datetime,
    refresh_expires_at: datetime,
    lifetime_policy: SessionLifetimePolicy,
    users: UserRepository,
    sessions: AuthenticationSessionRepository,
    tokens: RefreshTokenRepository,
) -> ProvisionalSessionEstablishment:
    """Opt-in admission at supplied created_at; neither provenance nor assurance.

    Trusted callers supply assertion, policy and time; forged timestamps are not detected.
    No hidden clock or freshness guarantee at lock acquisition, commit or delivery exists.
    Delegate once under the SAME caller transaction; propagate failures without retries.
    Export only after IdentityOperation.execute commits AND completes cleanup.
    Legacy establish_initial_session remains unchanged and does not enforce this policy.
    """
    if not isinstance(assertion, TrustedAuthenticationAssertion):
        raise ValueError("A trusted authentication assertion is required")
    if not isinstance(freshness_policy, AuthenticationFreshnessPolicy):
        raise ValueError("A validated authentication freshness policy is required")
    created_at = _utc(created_at)
    age = created_at - assertion.authenticated_at
    if age < timedelta(0):
        raise ValueError("Authentication cannot follow admission")
    if age >= freshness_policy.max_assertion_age:
        raise AuthenticationAssertionStale("Authentication assertion is stale")
    return await establish_initial_session(
        assertion=assertion,
        session_id=session_id,
        family_id=family_id,
        created_at=created_at,
        session_expires_at=session_expires_at,
        refresh_expires_at=refresh_expires_at,
        lifetime_policy=lifetime_policy,
        users=users,
        sessions=sessions,
        tokens=tokens,
    )
