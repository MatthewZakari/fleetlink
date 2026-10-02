# FL-009 Identity domain and persistence foundation

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
do not implicitly remove roles. Role switching/session behavior is deferred.

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
JWT/refresh tokens, password hashing, MFA, verification/recovery, sessions/devices,
authorization enforcement, scoped memberships, privileged grant workflows, audit persistence,
outbox, business profiles and Flutter UI. No runtime/tool dependency or lockfile change is
needed. The prescribed ownership and existing database namespace suffice without a new ADR.
See [validation commands](TESTING.md#fl-009-identity-validation).
