"""FL-012 possession primitives; no acceptance flow, persistence or transaction ownership."""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fleetlink.modules.identity.domain.refresh_token import RefreshTokenRecord, RefreshVerifier

_SECRET_BYTES = 32
_WIRE_LENGTH = 86
_VERSION = "flrt1"
_VERIFIER_PREFIX = b"flrt1:sha256:"
_HASH_CONTEXT = b"fleetlink.refresh-token\x00flrt1\x00"
_WIRE_PATTERN = re.compile(
    r"flrt1\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.([A-Za-z0-9_-]{43})"
)


class InvalidRefreshCredential(ValueError):
    """Rejected syntax only; never include the presented input in diagnostics."""


class RefreshCredentialGenerationError(RuntimeError):
    """Secure generation failed; no deterministic fallback is permitted."""


@dataclass(frozen=True, slots=True, eq=False)
class RefreshCredential:
    """Transient sensitive material, never a repository snapshot or diagnostic payload.

    Equality is object identity, not ordinary comparison of secret bytes. Do not use
    dataclass serialization, reflection or debugger locals to capture this object.
    """

    candidate_id: UUID
    _secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate_id, UUID)
            or type(self._secret) is not bytes
            or len(self._secret) != _SECRET_BYTES
        ):
            raise InvalidRefreshCredential("Invalid refresh credential")

    def reveal(self) -> str:
        """Explicit sensitive wire export; never log, persist or attach to telemetry."""
        return f"{_VERSION}.{self.candidate_id}.{_encode_secret(self._secret)}"


@dataclass(frozen=True, slots=True, eq=False)
class IssuedRefreshCredential:
    """Unpersisted generation result; caller supplies session, lifetime and transaction."""

    credential: RefreshCredential = field(repr=False)
    verifier: RefreshVerifier = field(repr=False)


def _encode_secret(secret: bytes) -> str:
    return base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")


def parse_refresh_credential(presented: object) -> RefreshCredential:
    """Validate only canonical v1 syntax. Parsing does not prove possession."""
    if type(presented) is not str or len(presented) != _WIRE_LENGTH:
        raise InvalidRefreshCredential("Invalid refresh credential")
    match = _WIRE_PATTERN.fullmatch(presented)
    if match is None:
        raise InvalidRefreshCredential("Invalid refresh credential")
    identifier, encoded = match.groups()
    # The bounded grammar guarantees valid UUID hex and base64url alphabet/length.
    secret = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    if _encode_secret(secret) != encoded:
        # Reject nonzero unused pad bits, not just invalid alphabet or decoded length.
        raise InvalidRefreshCredential("Invalid refresh credential")
    return RefreshCredential(UUID(identifier), secret)


def derive_refresh_verifier(credential: RefreshCredential) -> RefreshVerifier:
    """One-way v1 evidence, bound to the candidate UUID and protocol context."""
    digest = hashlib.sha256(
        _HASH_CONTEXT + credential.candidate_id.bytes + credential._secret
    ).digest()
    return RefreshVerifier(_VERIFIER_PREFIX + digest)


def generate_refresh_credential() -> IssuedRefreshCredential:
    """Use OS-backed randomness; never insert a record or acquire any resources."""
    try:
        credential = RefreshCredential(uuid4(), secrets.token_bytes(_SECRET_BYTES))
    except Exception:
        raise RefreshCredentialGenerationError("Refresh credential generation failed") from None
    return IssuedRefreshCredential(credential, derive_refresh_verifier(credential))


def verify_refresh_credential(presented: object, record: RefreshTokenRecord) -> bool:
    """Prove possession only, including against consumed evidence, without mutation.

    True is not refresh acceptance: callers must separately check lifecycle, session,
    account and authorization policy before using caller-owned atomic rotation.
    """
    try:
        credential = parse_refresh_credential(presented)
    except InvalidRefreshCredential:
        return False
    evidence = record.verifier.value
    if (
        credential.candidate_id != record.id
        or len(evidence) != len(_VERIFIER_PREFIX) + hashlib.sha256().digest_size
        or not evidence.startswith(_VERIFIER_PREFIX)
    ):
        return False
    derived = derive_refresh_verifier(credential).value
    return hmac.compare_digest(derived[len(_VERIFIER_PREFIX) :], evidence[len(_VERIFIER_PREFIX) :])
