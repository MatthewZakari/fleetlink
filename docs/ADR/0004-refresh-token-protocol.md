# ADR-0004 — Refresh token protocol and possession verification

- Status: Proposed (FL-012/FL-013 implementation; independent security review pending).
- Date: 2026-10-06.
- Owners/reviewers: Identity maintainers and independent security reviewer.
- Related tasks: FL-012 and FL-013; builds on FL-010 and FL-011.

## Context

FL-011 stores immutable, bounded one-way refresh evidence and performs caller-owned
atomic rotation. Its UUID lookup and reuse outcomes do not establish possession. It
deliberately leaves generation, parsing, derivation and cryptographic comparison to a
reviewed protocol. The existing `RefreshVerifier` accepts 1–512 immutable bytes.

## Decision and credential structure

Implement a small Identity application module using standard-library cryptographic
primitives. It depends only on the standard library and Identity domain types. No
repository, framework, service, transaction, clock or configuration is required.

The v1 wire grammar is `flrt1.<candidate-uuid>.<secret-base64url>` (placeholders only).
The candidate is a canonical lowercase hyphenated UUID, 36 ASCII characters. Generation
uses `uuid.uuid4()` independently of the secret. The secret is exactly 32 bytes from
`secrets.token_bytes(32)`, encoded as exactly 43 unpadded RFC 4648 base64url characters.
The complete credential is exactly 86 ASCII characters. Its UUID is public lookup
metadata; it is never proof of possession and contributes no assumed secret entropy.

Parsing bounds length before splitting/decoding, validates the complete grammar and
canonical UUID/encoding, and rejects whitespace, padding, alternate alphabets, nonzero
unused encoding bits, truncation, non-string input and unsupported versions. No input
normalization or fallback algorithm negotiation is permitted. Parsing establishes syntax
only. A frozen credential wrapper hides secret bytes from repr/str; `reveal()` is the
explicit, sensitive wire-export boundary. Generated output pairs this wrapper with its
one-way verifier; it is not a persistence snapshot.

## Randomness and verifier derivation

Production generation has no seed, entropy-size setting or injectable random-source
parameter. Tests temporarily patch the module's standard-library randomness calls with
synthetic bytes and UUIDs, scoped by pytest. No deterministic generator ships in runtime
code. Entropy failures produce a fixed safe error with no fallback or partial credential.

Derive the 32-byte digest with the standard SHA-256 implementation:

`SHA256(ASCII("fleetlink.refresh-token") || 0x00 || ASCII("flrt1") || 0x00 || UUID.bytes || secret)`.

This fixed-length, domain-separated encoding binds evidence to the protocol and candidate
identifier. It is ordinary hashing of a high-entropy random secret, not a password hash,
MAC, signature, encryption scheme or new cryptographic primitive. No server key or pepper
is required. This decision does not apply to passwords or other low-entropy inputs.

Persist `ASCII("flrt1:sha256:") || digest` in `RefreshVerifier`: exactly 45 bytes, within
the existing envelope. Neither raw secret nor its reversible base64 representation is
stored. Only the caller can construct and persist a `RefreshTokenRecord` with the generated
candidate ID, verifier, session owner and explicit lifetime.

## Verification and agility

Verification takes presented wire text and a detached FL-011 record. It parses, requires
candidate ID equality with that record, and requires the exact supported verifier prefix
and size. It derives the candidate digest and compares equal-length digest bytes using
`hmac.compare_digest`. Malformed inputs, wrong secrets, mismatched records and unknown or
invalid verifier envelopes return false without mutation. Public grammar, version, length
and UUID checks may short circuit; the complete operation is not claimed constant-time.
Only the secret-derived digest comparison has constant-time comparison semantics.

`flrt1` fixes both wire grammar and SHA-256 derivation. The evidence envelope independently
identifies version and algorithm. New constructions require a reviewed new version,
explicit parser/verifier allowlists, fixtures and a rollout/retirement policy. Never pass
an attacker-selected algorithm name to a generic crypto factory. Existing untagged FL-011
synthetic evidence remains storable but is not accepted by v1. No reinterpretation occurs.

## Threat model and replay

Identifier disclosure alone cannot authenticate. A read-only database disclosure exposes
one-way evidence and public metadata, not a usable credential; offline guessing must target
the independently generated 256-bit secret. Identifier binding prevents copying a verifier
to another record from making that credential valid there. Database write compromise,
runtime compromise and stolen bearer material are outside the protection of one-way storage.

A stolen credential is replayable. Possession verification deliberately also works against
consumed evidence: callers must prove possession before interpreting FL-011 reuse.
FL-013 implements the narrowly scoped response below, pending independent security review.
A true verification result does not check session/account state, expiry, currentness or
authorization and never rotates/revokes anything.
FL-011 lifecycle checks and session/token CAS remain mandatory in the FL-013 acceptance flow;
never automatically retry a losing CAS as an accepted refresh.

## FL-013 application composition and replay response

FL-013 implements the stable-session/family revocation boundary prescribed by FL-011,
using existing session revoke/save CAS for ACTIVE sessions after FL-012 proof of possession,
even if expired. Already-revoked families require no additional persistence mutation or
version increment. Incorrect secrets
and candidate UUIDs alone never trigger revocation. Confirmed consumed-credential reuse
revokes only its owning stable session/family, preserves evidence and never issues a new
credential. It cannot reactivate a session, mutate account state/roles or revoke unrelated
sessions. Conflicts propagate without automatic retry. This implements the prescribed
composition; it does not mark replay policy independently security-reviewed.

The explicitly approved FL-013 lifetime decision is **absolute, non-sliding expiration**:
replacement creation equals the explicit current operation time, and replacement expiry
equals the presented CURRENT record's expiry. Reject at or after that expiry and separately
check session expiry. Extending to session expiry, `now + duration`, configurable refresh
TTL and sliding sessions are rejected because they would expand existing lifetime policy.
Initial lifetime selection remains outside this milestone. ACTIVE accounts alone may
proceed; SUSPENDED/DISABLED accounts cannot refresh. Status is not authorization.

The caller owns one database transaction. A normal `ProvisionalRefresh` hides its credential
and permits only explicit sensitive export after commit. `ProvisionalRefreshReuse` is a
distinct denial outcome with no credential, returned normally so revocation can commit.
Neither outcome establishes issuance/durable response until commit; discard both on failure.
Exceptions escape the transaction and existing sanitized database boundary. Failed insertion
or replay response/commit must leave no partial writes. No generic Unit of Work or schema
change is needed. Tests cover real PostgreSQL competing rotations, revocation races and
rollback. Independent security review remains required; status stays Proposed.

## Diagnostics and logging restrictions

No logs, spans, events, metrics or persistence are emitted by the protocol. Errors contain
fixed text, never presented input. Do not log credentials, exported wire text, decoded
secrets, verifiers, dataclass serialization, snapshots, SQL parameters or object internals.
Do not use candidate IDs as metric labels. Existing FL-007/008 structural capture restrictions
remain mandatory; configuration-secret redaction is not a registry for generated credentials.

Raw material necessarily exists transiently in trusted process memory and at explicit
`reveal()` call sites. Python cannot guarantee erasure of immutable bytes/strings. Debugger
locals, core dumps, arbitrary reflection/serialization and third-party diagnostic handlers
must not capture it; repr masking cannot secure those surfaces. No token examples containing
real or live-looking secret material may be committed. Test vectors construct synthetic
material at runtime and must not print generated credentials.

## Alternatives rejected

- UUID-only credentials confuse lookup with possession and provide no separate secret.
- JWTs, signed/encrypted refresh tokens and external crypto packages add keys, dependencies
  and protocol surface without a need for self-contained claims in this stateful design.
- HMAC with a server pepper adds secret provisioning/rotation and recovery dependencies;
  high-entropy random secrets already resist offline guessing under the stated threat model.
- Adaptive password hashing is appropriate for low-entropy passwords, but adds unnecessary
  verification cost and denial-of-service exposure for independent 256-bit random secrets.
- Plain or reversibly encrypted token storage violates the one-way evidence requirement.
- SHA-256 without identifier/version binding permits accidental evidence reuse across records
  or protocol contexts. Handwritten cryptographic primitives are unnecessary.

## Consequences, compatibility and data impact

No dependency, lockfile, settings, schema or migration change is required. Alembic head stays
`0004_refresh_token_rotation`. Existing domain and repository contracts are unchanged.
Rollback restores prior code while retaining schema/evidence; older code cannot verify v1
and must not treat arbitrary verifier bytes as proof. Future protocol rollouts must account
for active credential lifetimes before retiring a verifier implementation.

FL-012 is the generation/verification foundation; FL-013 adds internal refresh orchestration.
Login/registration and HTTP authentication flows, middleware,
access JWTs, signing keys, OAuth/OIDC/PKCE, passwords, MFA/recovery, authorization, mobile UI,
audit/outbox and Redis/RabbitMQ authentication work remain deferred. Provider architecture
is not selected here. Phase 1 remains incomplete.

## Validation and follow-up

Require generation/uniqueness, deterministic synthetic derivation, strict parser rejection,
identifier binding, equal-length safe comparison, safe representations/errors, immutable
failure paths, no persistence during generation, and real PostgreSQL verifier round trips.
Run all prescribed backend, migration, privacy, scanner and adjacent regression gates.
Independent security review must assess the construction, future wire transport/storage
controls, abuse limits, replay response and version retirement before production exposure.

References: [Identity contracts](../IDENTITY.md), [security](../SECURITY.md),
[testing](../TESTING.md), [Python secrets](https://docs.python.org/3.12/library/secrets.html),
[SHA-256](https://docs.python.org/3.12/library/hashlib.html),
[safe comparison](https://docs.python.org/3.12/library/hmac.html#hmac.compare_digest),
[RFC 4648 encoding](https://www.rfc-editor.org/rfc/rfc4648.html).
