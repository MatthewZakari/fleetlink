# ADR-0005 — Identity account eligibility coordination

- Status: Proposed (FL-016 implementation; independent Phase C review pending).
- Date: 2026-10-13.
- Owners/reviewers: Identity maintainers and independent security reviewer.
- Related task: FL-016; extends FL-013 and FL-015, preserves FL-014.

## Context

FL-015 established sessions using an ACTIVE account snapshot; a suspension could commit
after that read but before session creation committed. FL-013 CURRENT refresh had the same
eligibility gap despite session/token CAS. A user FK protects existence, not status.
Credential ownership/provider selection remains unresolved. This task coordinates account
eligibility only; it does not produce or verify trusted authentication assertions.

## Decision

Add `UserRepository.get_for_authentication(UUID) -> User | None`. Its SQLAlchemy adapter
first selects only the user primary key with PostgreSQL `FOR SHARE`, then invokes the
existing joined snapshot read in a second statement. It locks no nullable role-join side.
The caller must already own the transaction (`autobegin=False`). The repository never
begins, commits, rolls back, creates savepoints or independently releases coordination.
No status/role/version mutation occurs; missing users return None without a gap lock.
A UUID remains lookup metadata, never authentication proof.

The supported composition uses PostgreSQL READ COMMITTED, the existing default verified
by integration tests. After a waiting lock statement finishes, the second statement uses
a fresh snapshot. Existing `populate_existing=True` refreshes cached ORM attributes as
well as reconstructing a detached immutable domain snapshot. Role writers through `save`
update the parent first, so their status/version/roles cannot change while readers hold it.
Direct role-table SQL is not this application contract. Ordinary `get()` remains nonlocking.

`FOR SHARE` conflicts with both UPDATE and NO KEY UPDATE row locks, including an ordinary
status-only UPDATE. Compatible authentication readers can proceed concurrently. `FOR KEY
SHARE` would not block status updates; relying on the FK is insufficient. A stronger
exclusive lock would unnecessarily serialize authentication readers for the same account.

At REPEATABLE READ or SERIALIZABLE, a changed row can produce a serialization failure
instead of a fresh READ COMMITTED result. Such failures must escape and roll back; no
automatic retry or alternate-isolation acceptance guarantee is added. Do not change the
deployment isolation level without reviewing this contract. Caller-selected operation times
are unchanged: waiting does not introduce commit-time expiry or assertion freshness checks.

## Ordering and transaction ownership

For establishment and CURRENT refresh, acquire account coordination before session/token
mutations: **user -> session -> token**. Initial creation inserts session then token;
rotation retains existing session CAS, token CAS and replacement insertion. Nonlocking
token/session lookup and refresh possession verification happen before account coordination.
No external provider calls or expensive verification may run while holding these locks.

Status/role `save()` keeps its existing user-row UPDATE/CAS before role replacement.
Authentication callbacks must not upgrade shared account locks into user writes, acquire
accounts after lineage writes, roll back a savepoint releasing coordination, or compose
multiple users in arbitrary order. Such new compositions require review; this milestone
coordinates one account per authentication operation. Shared-to-exclusive upgrades across
competing callbacks could deadlock. Database failures/conflicts propagate without retries.

Confirmed consumed-token possession keeps its account-independent branch: revoke only
the stable session through session CAS, with no account read or later account lock.
Already-revoked sessions need no mutation. Suspension cannot suppress confirmed reuse.
Session-only revocation likewise never acquires an account lock afterward.

The unchanged `IdentityOperation.execute()` owns commit and cleanup. All writes remain
provisional; export only after it returns. Holding a lock does not permit early disclosure.

## Guarantees and limits

If a status writer commits SUSPENDED/DISABLED first, a waiting authentication operation
observes ineligibility and writes no session/replacement evidence. If authentication gets
coordination first, the status writer waits until that transaction ends. Definite rollback
or pre-commit cancellation removes partial writes and releases coordination. Different
accounts are independent; same-account readers remain compatible, while status/role writes
may contend. Existing database command timeout bounds driver waits; tests additionally use
test-local `lock_timeout`. No new production timeout/default is selected.

This is ordering, not permanent ACTIVE eligibility. Suspension can commit after authentication
and before response delivery. Status changes do not revoke existing sessions; reactivation
can make an otherwise valid lineage eligible again. No account-wide revocation policy is
introduced. Assertions remain trusted-caller conventions without verified provenance,
freshness or single-use enforcement. Existing session/token CAS and absolute refresh expiry
remain unchanged, as do public route exposure and diagnostic privacy restrictions.

Lost commit acknowledgement or cancellation during commit can leave durable rows despite
an error. Post-commit cleanup/serialization/delivery failures cannot undo durable changes.
Release no provisional result on failure; never retry automatically. One-way evidence
cannot recover a lost credential. No database-and-network atomicity or reconciliation is
claimed. Do not log credentials, verifiers, snapshots, SQL text/parameters or debugger locals.

## Alternatives

- Repeated nonlocking reads merely move the race.
- A no-op user save mutates versions and roles and misuses aggregate mutation as coordination.
- FOR KEY SHARE/FK locking does not conflict with status-only updates.
- FOR UPDATE would protect eligibility but needlessly serialize same-account readers.
- Adding a lock to the outer join ignores nullable-side locking and post-wait role freshness.
- Serializable transactions introduce a broader isolation/retry policy; advisory locks would
  require every writer to cooperate with another locking protocol.
- Provider/password implementation is an independent ownership decision and does not remove
  this local account-state race.

## Compatibility, rollout and rollback

No schema, migration, dependency, settings, wire protocol or public API changes. Alembic head
remains `0004_refresh_token_rotation`. The repository port addition requires internal test
doubles/adapters to implement the coordinated method; there is no fallback to ordinary get.

Old application instances still use snapshot eligibility. Mixed old/new authentication
workers cannot claim the stronger guarantee; drain old instances and in-flight transactions
before relying on it. Existing status writers updating the same PostgreSQL row participate
without a new method. Application rollback preserves all data/evidence but restores the
old race; no destructive migration rollback is appropriate. Disable any future exposure
that relies on this guarantee before such rollback.

## Validation and follow-up

Unit tests prove ordering and unchanged denial, generation, reuse and lifetime behavior.
Real PostgreSQL tests observe `pg_blocking_pids()` with bounded synchronization to establish
both transaction orders, fresh status/role snapshots, status/role version preservation,
different-user progress, shared-reader compatibility, cancellation, timeout and rollback.
Existing CAS, privacy, commit-uncertainty, schema and migration regressions remain required.
See [testing](../TESTING.md#fl-016-account-eligibility-validation) for commands and scope.

Independent Phase C review must assess lock ordering, contention and rollout. Identity/security
owners still need to decide provider/credential ownership, production lifetimes, assertion
freshness, suspension/reactivation policy, recovery and delivery uncertainty before dependent
exposure. Phase 1 remains incomplete; ADR-0004 remains Proposed and no production
authentication readiness is established.

References: [Identity](../IDENTITY.md#fl-016-transactional-account-eligibility),
[database](../DATABASE.md#fl-016-account-row-coordination),
[security](../SECURITY.md#fl-016-transactional-eligibility-boundary),
[PostgreSQL row locking](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS),
[READ COMMITTED](https://www.postgresql.org/docs/16/transaction-iso.html#XACT-READ-COMMITTED).
