# Identity domain and persistence foundation

FL-009 begins Phase 1 and awaits independent review. It establishes canonical identities,
account lifecycle state and platform role assignment primitives. It does not complete
Phase 1, authentication, authorization or production Identity readiness.

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
CAS implement the already prescribed FL-010 architecture. No FL-012 scope is selected.
