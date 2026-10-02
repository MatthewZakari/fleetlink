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
JWT/refresh tokens, password hashing, MFA, verification/recovery, device management,
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

Deferred: token records, token generation/verification/rotation/reuse handling, login,
registration, refresh/logout, session/device HTTP management, JWT/JWKS/key management,
credentials/provider choice, OAuth/OIDC/PKCE, MFA, verification/recovery, authorization,
audit/outbox and all FL-011 work. No cross-cutting departure from prescribed Identity
ownership or persistence conventions requires a new ADR; credential/provider decisions
remain reserved for their own ADRs. See [validation](TESTING.md#fl-010-session-validation).
