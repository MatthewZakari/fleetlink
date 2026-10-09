# Identity domain and persistence foundation

FL-009 begins Phase 1 and awaits independent review. It establishes canonical identities,
account lifecycle state and platform role assignment primitives. It does not complete
Phase 1, authentication, authorization or production Identity readiness.

The sections below record successive milestone boundaries. FL-012 supplies the
refresh protocol previously deferred by FL-009/010/011. FL-013 composes the internal
refresh operation below; login/registration and all HTTP authentication flows remain deferred.
FL-014 adds the unmounted [HTTP boundary foundation](#fl-014-http-boundary-foundation).
See [the protocol contract](#fl-012-refresh-token-protocol-foundation).

## Ownership and model

Identity owns `User`: UUID `id`, timezone-aware UTC `created_at`, `AccountStatus`, an immutable
set of `PlatformRole` values and a nonnegative optimistic `version`. Callers supply creation
time explicitly and generate new IDs with standard-library `uuid.uuid4()`; deterministic
fixtures supply fixed UUIDs/times. Existing UUIDs survive reconstruction unchanged. This
application-owned strategy requires no extension, sequence or new dependency. Domain
construction rejects non-UUID IDs, naive timestamps, raw/invalid status or role values,
mutable role collections and invalid versions; aware times normalize to UTC.

Statuses are `active`, `suspended`, `disabled`. New snapshots default to active and no roles.
`with_status` allows replacement by any valid status; no unapproved transition or
reactivation policy is invented. Active status does not imply authentication/verification.
Roles follow PRODUCT.md: `customer`, `merchant`, `rider`, `administrator`. Assigning an
already assigned role and removing an unassigned role are deterministic no-ops in state.
One identity may hold all roles simultaneously. Removal may leave no roles. Status changes
do not implicitly remove roles. Role switching and authentication flows are deferred;
FL-010 session persistence is described below.

Snapshots are immutable; operations return validated replacements. Equality compares all
snapshot fields; use `id` for canonical identity comparisons across versions. Creation
metadata is immutable in the repository. Versions advance on each successful `save`, even
when state is unchanged; this is a persistence concurrency counter, not domain event order.

Roles represent eligibility only. They create no Commerce merchant profile or Logistics
rider profile, and grant neither arbitrary resource ownership nor administrator bypass.
Scoped memberships remain deferred. Future authorization combines roles with ownership,
membership, resource state and owning-context policy.

## Application and persistence contract

`UserRepository` exposes `add(User)`, `get(UUID) -> User | None` and `save(User) -> User`.
No ORM records or sessions cross this port. No HTTP use case is needed to prove persistence.
`add` requires version zero and inserts the user plus roles; duplicate IDs fail instead of
upserting. `get` returns a detached immutable snapshot or `None`. A single outer-joined
statement reads status/version and roles consistently, including users with zero roles.
`save` conditionally updates the user at the supplied version/creation time, replaces roles,
and returns the next version. Missing users raise `UserNotFound`; stale snapshots or changed
creation metadata raise `IdentityConflict`. Concurrent writers serialize on the user row;
one stale writer fails rather than losing another writer's assignment silently.

The caller composes `SqlAlchemyUserRepository(session)` inside `Database.session()` and
`session.begin()`. The adapter flushes but never begins, commits, closes or independently
rolls back a transaction. Let errors escape the transaction block so all writes roll back;
a retry must open a new transaction, reload and deliberately reapply the intended operation.
A returned saved snapshot is provisional until the caller commits. Do not reuse it after
rollback. Multiple repository operations can share one application-owned atomic transaction.
The existing session boundary sanitizes driver/constraint/transport failures into fixed
`DatabaseError`; do not expose raw SQLAlchemy exceptions caught inside that boundary.
No automatic retries, provider calls, generic repository or Unit of Work is introduced.

## Schema

| Table | Columns (all NOT NULL) | Constraints/indexes |
| --- | --- | --- |
| `identity_users` | `id UUID`, `status VARCHAR(16)`, `created_at TIMESTAMPTZ`, `version INTEGER` | `pk_identity_users(id)`; `ck_identity_users_status` allows active/suspended/disabled; `ck_identity_users_version` requires version >= 0 |
| `identity_user_roles` | `user_id UUID`, `role VARCHAR(16)` | `pk_identity_user_roles(user_id, role)` prevents duplicates; `ck_identity_user_roles_role` allows the four roles; `fk_identity_user_roles_user` references Identity users with ON DELETE CASCADE |

The primary keys supply all needed indexes; the composite key begins with user_id for
role lookup and foreign-key cleanup. Cascade prevents orphaned assignments if infrastructure
explicitly deletes a user; no application deletion API or retention policy is added.
No defaults silently generate IDs/timestamps/status, and no personal profile, credential,
token, JSON, soft-delete or cross-context table is introduced. PostgreSQL preserves timestamp
instants; repository reconstruction normalizes to UTC regardless of session timezone.

Explicit mappings register on the existing metadata and are deliberately imported by
Alembic. The existing reflected-unowned-table filter still excludes PostGIS/unrelated
objects. Revision `0002_identity_foundation` follows the untouched technical baseline.
See [migration operations and destructive downgrade](DATABASE.md#fl-009-identity-schema-and-migration).
Downgrade deletes Identity data permanently; re-upgrade only recreates empty tables.

## Compatibility and deferred work

Database enablement, connection lifecycle, cancellation shielding, health/readiness,
Redis/Celery and telemetry/secret settings remain unchanged. Importing mappings creates no
connections and startup never creates tables. Persistence-disabled startup is supported.
There are no Identity logs/spans or UUID metric labels; existing SQL instrumentation captures
neither statements nor parameters. Fixtures use only synthetic data.

Deferred: credentials/federation/provider choice, authentication/registration endpoints,
JWT/refresh-token protocols, password hashing, MFA, verification/recovery, device management,
authorization enforcement, scoped memberships, privileged grant workflows, audit persistence,
outbox, business profiles and Flutter UI. No runtime/tool dependency or lockfile change is
needed. The prescribed ownership and existing database namespace suffice without a new ADR.
See [validation commands](TESTING.md#fl-009-identity-validation).

## FL-010 authentication-session foundation

FL-010 implements durable session lineages, pending independent review. It does not
implement authentication and does not complete Phase 1. Identity owns
`AuthenticationSession`: UUID `id`, `user_id`, `family_id`, UTC-aware `created_at` and
`expires_at`, `SessionStatus` (`active` or `revoked`), and nonnegative integer `version`.
No account status, roles or profiles are copied. Session existence and active lifecycle
are not proof of authentication, eligibility or permission.

Construction rejects non-UUID identifiers, naive/non-datetime timestamps, invalid expiry
ordering, raw status strings and invalid versions (including booleans). Timestamps
normalize to UTC. Frozen snapshots compare all fields; reconstruction preserves identity.
`revoke()` returns a revoked snapshot and is idempotent in state. There is no reactivation
operation. `is_expired(at)` requires explicit aware time; expiry is true at and after
`expires_at` without changing the persisted lifecycle. No method reads the system clock.
Creation/expiry times, user and family are immutable through persistence; extending a
lineage lifetime requires separately reviewed policy, not a blind save.

### Family and future rotation boundary

One session is the stable owner of exactly one refresh lineage. `family_id` is unique
across session rows, including revoked rows; rotating a future token must not create a
second session in that family. Multiple independent sessions for one user use distinct
families. This makes family lookup/revocation unambiguous and prevents a family from
being attached to two users. `get_by_family` includes revoked and time-expired sessions.
Revoking this stable session revokes the lineage state; no token validation flow exists.

This revision deliberately stores **no tokens or verifiers**. UUID identifiers are not
bearer credentials. No cryptographic protocol is selected, no token is generated, and no
plaintext, reversible ciphertext, password hash or generic token-text column is present.
A future reviewed token-record design must preserve consumed/replaced verifier records,
link them to this stable session, distinguish current versus consumed state, and detect
reuse. It must conditionally advance this session's version and atomically consume/replace
its token records in the same caller-owned transaction, checking revocation and explicit
expiry. Only one competing write at a given version can succeed. A successful session
save alone is not refresh acceptance or replay detection; old-token identification and
reuse response cannot exist until those records and the verification contract are added.
Do not automatically retry a failed refresh CAS as a new accepted refresh.

### Repository and schema

`AuthenticationSessionRepository` provides `add`, `get`, `get_by_family`, and `save`.
The SQLAlchemy adapter follows `UserRepository`: detached immutable snapshots only,
caller-owned `Database.session()` and `session.begin()`, flush allowed, no internal
begin/commit/close/rollback. User creation and session creation can compose atomically.
`add` requires version zero; absent users and duplicate IDs/families fail through database
integrity and the existing sanitized `DatabaseError` boundary. `get`/family lookup return
`None` when missing. `save` raises `SessionNotFound` for missing IDs and `SessionConflict`
for stale versions, altered immutable metadata or reactivation, with fixed messages.
Let failures escape the outer transaction so all composed writes roll back.

Conditional SQL compares persisted version and immutable metadata and allows only
active-to-active, active-to-revoked or revoked-to-revoked saves. Even a fabricated current
version cannot reactivate a revoked row through the adapter. Direct privileged SQL is not
an application interface. Every successful save increments version, including a repeated
revocation: this is a persistence concurrency counter, not an authentication-event sequence.
Returned versions are provisional until caller commit; discard them after rollback.

`identity_auth_sessions` has seven NOT NULL columns: UUID `id`, `user_id`, `family_id`;
TIMESTAMPTZ `created_at`, `expires_at`; VARCHAR(16) `status`; INTEGER `version`.
Named constraints: `pk_identity_auth_sessions`, `fk_identity_auth_sessions_user`,
`uq_identity_auth_sessions_family`, and `ck_identity_auth_sessions_status`,
`ck_identity_auth_sessions_version`, `ck_identity_auth_sessions_expiry`.
Checks enforce bounded status, version >= 0 and expiry > creation. The family uniqueness
index is the only non-PK index: it enforces one lineage owner and supports the implemented
family lookup. No user listing/status/expiry index is added without an access path.

The user FK uses **ON DELETE RESTRICT**. Unlike role assignments, sessions may be security
evidence; automatic user deletion must not silently erase them. Retention, erasure and
legal-hold policy remain unresolved; future reviewed deletion must explicitly handle
session records first. This is not an indefinite-retention policy or deletion API.
No database UUID/time defaults or startup table creation are added. Alembic remains owner;
[rollback destroys only FL-010 data](DATABASE.md#fl-010-session-schema-and-migration).

There are no new logs, spans, settings, metric labels, dependencies or lockfile changes.
Never log snapshots, identifiers as metric labels, token/verifier material, cookies,
Authorization headers or SQL/parameters. FL-007 capture bounds and FL-008 diagnostic
sanitization remain intact. Future verifiers must have a bounded, explicitly classified,
algorithm-neutral storage contract until the cryptographic protocol is reviewed.

Deferred after FL-010: token records (added by FL-011 below), token generation/verification,
refresh flows and reuse response, login,
registration, refresh/logout, session/device HTTP management, JWT/JWKS/key management,
credentials/provider choice, OAuth/OIDC/PKCE, MFA, verification/recovery, authorization,
audit/outbox. FL-011 extends persistence below. No cross-cutting departure from prescribed Identity
ownership or persistence conventions requires a new ADR; credential/provider decisions
remain reserved for their own ADRs. See [validation](TESTING.md#fl-010-session-validation).

## FL-011 refresh-token rotation foundation

FL-011 adds internal domain/persistence primitives, pending independent review. It does not
implement authentication or complete Phase 1. Identity owns `RefreshTokenRecord` beneath
one existing `AuthenticationSession`; user roles, account status and authorization decisions
are never copied into it.

| Field | Meaning |
| --- | --- |
| `id: UUID` | Non-secret opaque candidate lookup identifier; possession proves nothing |
| `session_id: UUID` | Stable session/family owner |
| `verifier: RefreshVerifier` | Sensitive immutable one-way material, 1–512 bytes |
| `created_at`, `expires_at` | UTC-aware lifetime, expiry strictly after creation |
| `status` | `current` or terminal `consumed` |
| `replaced_by_id: UUID or None` | Different replacement record, mandatory when consumed |
| `consumed_at: datetime or None` | UTC-aware consumption instant within the old lifetime |
| `version: int` | Nonnegative optimistic persistence counter; booleans rejected |

Aware timestamps normalize to UTC, following FL-009/FL-010. Snapshots are frozen and compare
all state; record ID is identity across versions. Verifier bytes and the containing verifier
field are excluded from repr/str; exception messages contain only fixed text. The wrapper's
`value` exists solely for trusted persistence and future verification. Never serialize
snapshots (including dataclass `asdict`), inspect them in shared diagnostics, or log verifier
values. Memory/debug access is not secured by repr omission. There is no global secret
registry and no new telemetry capture.

### Identifier and verifier boundary

Lookup uses the UUID, never a bearer token or verifier. Verifier bytes must already be
one-way evidence from a future reviewed protocol. The type checks size and immutability;
it cannot establish that caller-supplied bytes were cryptographically derived. No raw bearer
refresh token, reversible ciphertext, cookie or Authorization header may be supplied.
The 512-byte cap is a storage envelope, not an algorithm, security-strength claim, public
wire format or token-generation protocol. No uniqueness constraint is imposed on verifier
bytes because that protocol is not selected. No token generation, possession verification,
constant-time comparison or cryptographic library is added.

### Lifecycle, expiry and reuse

`CURRENT -> CONSUMED -> replaced_by_id` preserves previous evidence; the linked record
starts CURRENT. Current records have no consumption metadata. Consumed records require both
fields and `created_at <= consumed_at < expires_at`. Self-replacement is invalid. There is
no reactivation, metadata editing, deletion API or idempotent successful consumption.
`consume(replacement_id, at)` returns a new snapshot without advancing persistence version.

`is_expired(at)` is true exactly at expiry and thereafter, with no database state mutation.
All decisions receive explicit aware time; no domain clock is read. `require_current(at)`
raises `RefreshTokenReuse` for consumed records, even after expiry, and
`RefreshTokenExpired` for expired current records. Evaluation before creation is invalid.
Missing `get` returns `None`, distinct internally from current, expired and consumed.
These are internal evidence outcomes, not proof of bearer possession or public responses.
Future handling must verify possession before treating reuse as confirmed and should revoke
the relevant stable session/family under an explicitly reviewed policy. Existing session
`revoke`/`save` supplies terminal revocation; FL-011 adds no automatic or account-wide policy.

### Persistence and atomic rotation

`RefreshTokenRepository` exposes `add`, `get` and `rotate(token, replacement, session, at=...)`.
`rotate` is the conditional mutation operation in place of an unrestricted `save` that could
consume evidence without the session concurrency boundary. `add` inserts CURRENT/version-zero
records only; it is an internal initial-evidence insertion primitive, not session acceptance.
Session FK and one-current uniqueness apply even to initial insertion. `get` returns detached
snapshots. Duplicate IDs, absent sessions and invalid database constraints use the existing
sanitized `DatabaseError` boundary.

The pure domain `prepare_rotation` service requires a current, unexpired old token,
an active unexpired session, matching
session ownership, and token lifetimes within the session lifetime. The replacement is new,
CURRENT/version-zero, with creation equal to the supplied consumption instant. It may not
extend past the stable session expiry. The adapter first conditionally saves the supplied
active session using the FL-010 repository, advancing its version. It then conditionally
consumes the old record, comparing version, current lifecycle, empty consumption metadata
and all immutable fields (including verifier), and advances that token version. Finally it
inserts the replacement. The replacement FK is deferred until commit so consuming first
releases the unique current slot. Replacements cannot reuse an existing record ID.

Session-first ordering serializes against revocation and other lineage writes. Two competing
rotations cannot both succeed. Missing token writes raise `RefreshTokenNotFound`; token CAS
or immutable-state failures raise `RefreshTokenConflict`; session CAS retains FL-010
`SessionNotFound`/`SessionConflict`. Supplied revoked/expired sessions raise
`RefreshSessionUnavailable`. A stale previously-current snapshot yields conflict, not success;
reload and verify evidence before interpreting it as confirmed reuse. Never automatically
retry a losing CAS as accepted refresh. Fabricating a new version cannot erase consumption,
change replacement metadata, reactivate a token or revive a revoked session through this port.

Compose all work inside `Database.session()` and explicit `session.begin()`. No repository
begins, commits, closes or independently rolls back a transaction. **Every rotation failure
must escape the outer transaction**, including application conflicts after the session CAS;
catching and committing partial work violates the port contract. Rollback restores both
versions and old-token state and removes inserted replacements. Returned `RefreshRotation`
snapshots are provisional until caller commit and must be discarded after rollback. No
savepoint, automatic retry, independent Unit of Work or outbox is introduced.

### Schema and operational limits

`identity_refresh_tokens` has the nine fields above: PostgreSQL UUIDs, BYTEA, TIMESTAMPTZ,
VARCHAR(16) and INTEGER. Only `replaced_by_id` and `consumed_at` are nullable. Named checks
`ck_identity_refresh_tokens_{status,version,expiry,verifier,replacement,lifecycle}` enforce
allowlisted state, version >= 0, lifetime ordering, byte bounds, no self-link and lifecycle
field/time consistency. `pk_identity_refresh_tokens` supplies UUID lookup.
`uq_identity_refresh_tokens_current` is the only additional index: a partial unique index
on `session_id WHERE status = 'current'` enforcing a single lineage head, including an
expired current record. Expiry alone does not free the slot or authorize another lineage.

`fk_identity_refresh_tokens_session` and the deferred self-FK
`fk_identity_refresh_tokens_replacement` use ON DELETE RESTRICT to preserve security evidence.
Retention/erasure remains a future reviewed policy, not indefinite retention. The adapter
enforces same-session replacement and terminal transitions; privileged direct SQL is not
an application interface and could bypass temporal transition rules, as with FL-010.
No speculative lookup indexes, verifier uniqueness or defaults are added.

Revision `0004_refresh_token_rotation` follows `0003_auth_session_foundation`; the shorter
revision name fits the existing Alembic VARCHAR(32) revision column. Upgrade is additive.
Downgrade to FL-010 permanently destroys refresh evidence only, preserving users, roles,
sessions, PostGIS and unrelated objects. Re-upgrade creates an empty refresh table; it
cannot recover evidence. Prefer code rollback retaining schema when data must survive.
See [migration operations](DATABASE.md#fl-011-refresh-token-schema-and-migration).

Deferred: token protocol/generation/verification, refresh acceptance and reuse response,
registration/login/refresh/logout/session/device HTTP endpoints, middleware, headers/cookies,
access tokens, JWT/JWKS/signing keys, OAuth/OIDC/PKCE/provider/password ownership, hashing,
MFA, verification/recovery, authorization/RBAC/memberships, business profiles, audit/outbox,
mobile UI and production readiness. FL-011 requires no Redis/RabbitMQ, dependency or
lockfile changes. No ADR is necessary: algorithm-neutral evidence and caller-owned session
CAS implement the already prescribed FL-010 architecture. FL-012 extends this boundary below.

## FL-012 refresh-token protocol foundation

FL-012 implements generation and possession verification only, pending independent security
review. Phase 1 remains incomplete. [Proposed ADR-0004](ADR/0004-refresh-token-protocol.md)
defines the exact construction, threat model, alternatives and evolution policy.

`identity/application/refresh_protocol.py` uses only standard-library facilities and the
existing domain snapshots. It imports no repository, database, HTTP, logging or telemetry
code. It exposes synchronous typed primitives without a service container or new port:

| Interface | Contract |
| --- | --- |
| `generate_refresh_credential()` | Returns `IssuedRefreshCredential(credential, verifier)`; independently generates a UUIDv4 and 32 cryptographically random bytes; no record, session, clock or transaction is created |
| `RefreshCredential` | Frozen transient candidate UUID and hidden secret bytes; repr/str omit secret material and equality uses object identity |
| `credential.reveal()` | Explicit sensitive export of the canonical wire string; never send to diagnostics or persistence |
| `parse_refresh_credential(presented)` | Bounds and validates external input; returns a credential or fixed `InvalidRefreshCredential`; syntax alone proves nothing |
| `derive_refresh_verifier(credential)` | Produces a domain-separated SHA-256 verifier bound to the candidate UUID |
| `verify_refresh_credential(presented, record)` | Returns a possession boolean against a detached `RefreshTokenRecord`; malformed credentials, mismatched IDs, incorrect secrets and unsupported evidence fail closed without mutation |

The wire grammar is `flrt1.<candidate-uuid>.<secret-base64url>` (placeholders, not a usable
credential). Exactly 86 ASCII characters comprise a lowercase hyphenated UUID and 43
unpadded base64url characters representing 32 secret bytes. Parsing rejects alternate UUID
spellings, whitespace, padding, invalid alphabet, noncanonical unused bits, wrong sizes and
unknown versions. Generation uses `secrets.token_bytes(32)` and `uuid4()` with no seed or
runtime randomness override. Tests patch those calls temporarily with synthetic fixtures.
Random-source failure raises fixed `RefreshCredentialGenerationError`, with no fallback.

Evidence is the 13-byte ASCII prefix `flrt1:sha256:` followed by a 32-byte SHA-256 digest:
45 bytes in the unchanged FL-011 `RefreshVerifier` envelope. The exact preimage is specified
in ADR-0004. Verification requires the supported prefix/size and candidate UUID equality,
then uses `hmac.compare_digest` on equal-length digest bytes. Public syntax/ID checks can
short circuit; no whole-operation or database-lookup timing guarantee is claimed.
Unknown versions/algorithms never fall back. Future versions require explicit reviewed
allowlists and rollout/retirement policy; untagged FL-011 synthetic evidence is not v1 proof.

The caller supplies `credential.candidate_id` and `issued.verifier` when constructing a
record, along with the stable session owner and explicit lifetime. Only that record may
cross the repository port. Generation itself never persists it or owns a transaction.
Verification accepts consumed evidence so a future caller can distinguish proven reuse
from an identifier-only attack. A true result is **not refresh acceptance**: it does not
check expiry, session/account lifecycle or authorization, consume a record, rotate a token,
revoke a family or retry a CAS. Existing FL-011 contracts remain unchanged.

Raw material exists only transiently in trusted memory and explicit wire exports. Never
serialize credentials with `asdict`, pickle, object reflection or debugger-local capture;
never log/export secrets, verifiers, snapshots or raw SQL parameters. Repr masking is not
memory isolation or secure erasure. No logs, metrics, spans or global secret registry are
added. FL-007/008 diagnostic policies still apply, including their direct-SDK/debug limits.

There is no schema, migration, dependency, lockfile, settings or HTTP contract change.
Rollback retains evidence and schema; earlier code has no v1 verifier and cannot accept it.
Authentication/registration/login/refresh/logout flows, session/device HTTP management,
middleware, access JWTs/JWKS/signing keys, provider/password architecture, OAuth/OIDC/PKCE,
password hashing, MFA/recovery, authorization/RBAC/memberships, business profiles, mobile UI,
audit/outbox and Redis/RabbitMQ authentication integration remain deferred. See
[validation](TESTING.md#fl-012-refresh-protocol-validation).

## FL-013 refresh authentication service

`identity/application/refresh_authentication.py` composes the existing typed ports and
FL-012 protocol. It adds no transport, dependency, setting or migration. Phase 1 remains
incomplete; this implementation awaits independent review and does not establish production
Identity readiness. No login/registration HTTP flow, HTTP refresh/logout, access JWT/JWKS,
authentication middleware, provider/password architecture, OAuth/OIDC/PKCE, authorization,
MFA/recovery or mobile authentication UI is implemented.

`authenticate_refresh(presented, at=..., tokens=..., sessions=..., users=...)` requires
explicit aware time (normalized to UTC) and adapters bound to the **same caller-owned
transaction**. Parsing rejects malformed/unsupported input. Only the public candidate UUID
is used for lookup. Missing evidence and incorrect possession cause no mutation. FL-012
verification against the detached record must succeed **before** consumed status is
interpreted as confirmed reuse. Candidate ID possession alone can never revoke a session.

CURRENT evidence must pass `require_current(at)` before session/account loading or generation.
The owning session must match, be active and unexpired, with compatible token lifetime.
`require_rotation_session` extracts the existing FL-011 lineage checks so both orchestration
and `prepare_rotation` use one authoritative rule. The owning user must exist, match the
session owner and be ACTIVE. SUSPENDED and DISABLED are rejected. This is account lifecycle
eligibility, not authorization; roles are neither interpreted nor copied into credentials.
Account status is checked from the repository snapshot; this milestone adds no account-wide
locking or atomic account-status-change/revocation policy.

### Absolute, non-sliding expiry

For every accepted CURRENT rotation, replacement creation is the explicit operation time
and replacement expiry is exactly the presented current record's `expires_at`. At or after
that expiry rotation fails. Rotation never extends expiry to the session expiry, computes
`now + duration`, introduces a TTL setting or creates sliding sessions. The stable session
expiry is an independent upper bound. This preserves the lineage's already established
absolute credential lifetime; initial lifetime selection remains outside FL-013.

Generation uses FL-012 only after lifecycle checks. Only candidate UUID, derived verifier,
session owner and explicit times enter replacement evidence. FL-011 `rotate` performs
session CAS, old-token consumption and replacement insertion; the service adds no SQL or
retry logic. A stale version is a conflict, never an automatically retried success.

### Confirmed reuse and transaction outcomes

Correct possession of CONSUMED evidence ensures its stable session/family is revoked and
returns `ProvisionalRefreshReuse`. An ACTIVE session uses terminal `session.revoke()` plus
existing optimistic `sessions.save()`, even when the record/session has expired. An already
REVOKED session requires no additional persistence mutation; its version remains unchanged.
It issues no replacement and preserves consumed history. It does not load or mutate
account state, remove roles, revoke other devices, disable/suspend users or initiate recovery.
A revocation CAS conflict propagates and must roll back; no automatic retry is performed.

A normal rotation returns `ProvisionalRefresh`, containing only a hidden credential wrapper
and explicit sensitive `reveal()` export. **Neither result is final until the surrounding
transaction commits.** The service cannot observe commit. Keep the result inside trusted
application memory, exit the caller's transaction and Database session boundary successfully,
and only then issue the replacement or treat replay revocation as durable. Discard results
on rollback, cancellation or commit failure. Never issue a credential before commit.
For confirmed reuse, return normally through the transaction to commit revocation; do not
raise a denial exception inside it and inadvertently undo the response.

Every exception must escape the transaction and `Database.session()` boundary. The latter
sanitizes driver/constraint/commit failures into existing `DatabaseError`. Repositories and
the service never begin, commit, close or independently roll back; no Unit of Work is added.
Internal distinctions reuse `InvalidRefreshCredential`, `RefreshTokenNotFound`,
`RefreshTokenExpired`, `SessionNotFound`, `RefreshSessionUnavailable`, `SessionConflict`,
`RefreshTokenConflict` and `InvalidRefreshRotation`. Only possession failure and account
unavailability add fixed-message exceptions. Generation errors retain the FL-012 contract.
These distinctions have no public/HTTP mapping and must not become enumeration responses.

Session-first CAS serializes competing rotations and revocation. Exactly one writer from
the same session version can win; a rotation that loses to revocation cannot commit. If
rotation wins first, a competing stale revocation conflicts; a subsequent deliberate
revocation invalidates the replacement. Confirmed reuse never reactivates the lineage.
Failed insertion or replay response/commit rolls back all writes in that transaction.

No logs, spans, metrics, identifier labels, raw credential persistence, SQL parameter
capture or global redaction registration is added. Repr/str omit credential material;
arbitrary serialization, debugger locals and process memory remain sensitive as in FL-012.
Rollback restores prior application code while retaining schema and evidence, including
committed revocations. Alembic head remains `0004_refresh_token_rotation`.
See [ADR-0004](ADR/0004-refresh-token-protocol.md) (still Proposed) and
[validation](TESTING.md#fl-013-refresh-authentication-validation).

## FL-014 HTTP boundary foundation

FL-014 adds `modules/identity/interface/http`, pending independent review. It is an
adapter foundation, not an authentication API. No router is mounted by `create_app`;
`/health`, `/ready`, `/openapi.json` and their contracts remain unchanged. Domain,
application, protocol and persistence semantics are unchanged. ADR-0004 stays Proposed.

### Composition and transaction ownership

Future explicitly authorized handlers inject `IdentityOperationDependency`. Native FastAPI
caching supplies one `IdentityOperation` per request from the existing `get_database`
dependency; disabled/out-of-lifespan resources fail through Platform's sanitized 500.
Dependency resolution opens no session or connection, so rejected request bodies do not
start transactions. There is no global container, new engine or import-time I/O.

The handler calls `await operation.execute(work)` exactly once. The async callback receives
`IdentityServices`: the existing user, authentication-session and refresh-token ports, all
bound to the same session, plus a thin call to the existing `authenticate_refresh` service.
The callback composes only Identity-owned application work. It must propagate every failure,
never independently commit/rollback, retain ports, spawn background work, send a response,
serialize domain snapshots or reveal credentials. A second execution on the same request
object fails rather than creating separately committed partial operations or retrying a CAS.

`IdentityOperation` is the HTTP caller/transaction owner. It acquires `Database.session()`,
explicitly begins, invokes the callback, commits once on normal completion, and closes the
session before returning the result. Callback/commit exceptions and cancellation trigger
shielded rollback; the existing session boundary shields close and sanitizes driver errors.
Begin failures still pass through session cleanup. Repositories and domain objects remain
unchanged and never commit. No generic Unit of Work, savepoint or retry policy is introduced.

Only after `execute` returns may a future authorized endpoint explicitly export sensitive
wire material. `ProvisionalRefreshReuse` must return normally from the callback so its
revocation commits; translate it to `AuthenticationDenied` **after** `execute` returns.
Never raise that transport denial inside the callback for a successful reuse response.
Failed commits return no result to the handler, including results with provisional credentials
or revocations. The adapter does not reinterpret expiry, account state or replay policy.

The operation callback is trusted application composition, not a sandbox: Python cannot
prevent it from retaining/exporting values early. Endpoint review must enforce this contract.
Construct and validate ordinary safe response projections inside the callback when possible.
A response serialization or network failure after commit cannot undo committed work. A lost
commit acknowledgement can leave durability uncertain; return an error, discard the result
and never automatically retry refresh acceptance. Future endpoints need separately reviewed
reconciliation/idempotency behavior. This foundation does not claim atomic database-and-network
issuance, successful delivery, or rollback of an already committed transaction.

### Error and schema contract

Future authentication routers use `APIRouter(route_class=IdentityRoute,
responses=identity_problem_responses())`. This factory combination is demonstrated only in
`apps/api/tests/test_identity_http.py` and `apps/api/tests_db/test_identity_http_postgres.py`.
No production sample route, login DTO, token response model or OpenAPI security scheme is added.
Future endpoint work must define strict, bounded request DTOs (`extra="forbid"`), explicit
safe response models, credential transport and the applicable `WWW-Authenticate` challenge;
FL-014 does not select or implement a public authentication protocol.

The adapter reuses Platform's seven-field `application/problem+json` envelope, URN instance,
correlation validation, no-store and other security headers. `AuthenticationProblem` narrows
status/title/code/detail to a typed fixed denial. No optional field diagnostics are emitted.

| Condition | Status | Code | Detail |
| --- | --- | --- | --- |
| Allowlisted authentication/application/domain denial | 401 | `authentication_failed` | `Authentication failed.` |
| Request validation, including unknown writable fields in strict DTOs | 422 | `validation_error` | `Request validation failed.` |
| Database, commit/rollback/close, generation, response validation or unknown failure | 500 | `internal_error` | `An unexpected error occurred.` |

The denial allowlist includes malformed refresh credentials, failed possession, unavailable
accounts/sessions, missing users/sessions/tokens, expiry, reuse and optimistic conflicts,
plus the explicit post-commit transport denial. All share one response shape and message;
no account existence, lifecycle state, verifier, SQL, exception text or conflict detail is
exposed. Conflict exceptions still roll back and are never retried. `ValueError` and invalid
internal rotation construction are not blindly classified as user input errors. Credential
generation errors remain server failures. Unknown exceptions retain Platform handling and
privacy-safe logging. Framework HTTP exceptions retain their existing sanitized contracts.

This allowlist is for the authentication boundary only. Future authorized Identity management
APIs need their own disclosure-safe 403/404/409 contracts. Uniform error content does not
establish timing uniformity, enumeration resistance under timing analysis or abuse protection.

### Privacy, compatibility and deferred work

There are no new logs, telemetry attributes, secret registries or configuration fields.
Never capture request bodies, headers, raw credentials, evidence, snapshots, SQL parameters
or arbitrary exception diagnostics. FL-007/008 structural capture bounds remain authoritative.
The test-only demonstrations use synthetic data and never expose raw credentials over HTTP.

There are no migrations, production/development dependencies or lockfile changes. Alembic
head remains `0004_refresh_token_rotation`. Rollback removes this unmounted package and its
tests/docs without changing persisted data; no deployed caller or schema migration is needed.
This follows existing Clean Architecture/native DI and caller-owned transaction conventions,
so no new architectural deviation ADR is required. ADR-0004 remains Proposed.

Public login, registration, refresh, logout, token validation, JWT/JWKS, authentication
middleware, passwords/providers, OAuth/OIDC/PKCE, MFA, Flutter auth, authorization, rate
limiting, outbox and production readiness remain deferred. Phase 1 remains incomplete.
See [validation](TESTING.md#fl-014-http-boundary-validation).
