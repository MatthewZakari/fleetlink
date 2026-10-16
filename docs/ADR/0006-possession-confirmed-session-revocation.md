# ADR-0006 — Possession-confirmed single-session revocation

- Status: Proposed (FL-017 architecture approved; independent Phase C code review pending).
- Date: 2026-10-16.
- Owners/reviewers: Identity maintainers and independent security reviewer.
- Related task: FL-017; builds on FL-010 through FL-016.

## Context

Identity has strict refresh possession verification, retained consumed evidence and terminal
session CAS. FL-013 revokes a family upon confirmed consumed reuse, but CURRENT evidence has
no dedicated application termination operation. SECURITY.md requires session controls before
public authentication release. This internal milestone adds only termination of the lineage
owned by presented evidence; it does not establish authentication provenance or public logout.

## Decision

`revoke_session_by_refresh_credential` parses with FL-012, loads evidence by candidate UUID,
verifies possession, then loads the session using the evidence's persisted session ID.
It checks returned session identity before mutation. UUID possession alone grants nothing.
Existing fixed-message errors distinguish internal failures without echoing sensitive inputs.

Both CURRENT and CONSUMED evidence, including expired evidence, may terminate their own
lineage after successful verification. Session expiry and account eligibility do not restrict
this reduction of capability. No user repository is accessed. ACTIVE sessions use existing
`revoke()` and optimistic `save`; already REVOKED sessions require no write/version increment.
The same empty immutable provisional result represents either case. No credential generation,
rotation, token mutation, account mutation, identifier output or unrelated revocation occurs.

This is limited possession-based termination authority, not a general authenticated principal,
resource permission, device-management interface or access-token validation mechanism.
No provider, password strategy, JWT algorithm, transport or lifetime is selected.

## Transactions and concurrency

Ports share one caller-owned transaction. Compose directly in the unchanged FL-014
`IdentityOperation.execute` callback; completion is durable only after commit and cleanup.
All exceptions escape. No savepoint, new runner, implicit commit or automatic retry is added.
Revocation acquires only session CAS and never subsequently acquires account coordination.
FL-016 account -> session -> token ordering for acceptance remains unchanged.

Refresh and revocation from the same session version cannot both win. If refresh commits
first, stale revocation conflicts and must not report success. A later deliberately initiated
operation may verify retained evidence and revoke the latest session snapshot. If revocation
commits first, stale refresh conflicts and later refresh fails session eligibility. Confirmed
reuse and explicit termination also compete through session CAS. Sequential repeated
termination is a no-op; concurrent repetition may conflict. Revocation is terminal.

Definite rollback/pre-commit cancellation restores state and releases locks. Lost commit
acknowledgement, cancellation during commit or post-commit cleanup failure can leave durable
revocation despite an error. Return no success on failure; do not infer rollback from an error
or retry automatically. No database/network atomicity or reconciliation is promised.

## Alternatives and tradeoffs

- UUID-only termination permits targeted denial of service without possession proof.
- Requiring an ACTIVE account would obstruct termination while suspended or disabled.
- Rejecting historical/expired evidence would prevent this narrow termination authority even
  though consumed possession already authorizes FL-013 family revocation.
- Rotating before revocation generates needless secrets and changes evidence unnecessarily.
- Retrying stale CAS hides contention and changes the established caller-owned retry contract.
- Global revocation, provider authentication and JWT work require additional policy decisions.

A stolen credential, including historical evidence, can terminate its own family. This
availability tradeoff does not extend its ability to authenticate or affect another family.
Evidence retention bounds the availability of this proof; retention/erasure policy is deferred.
Public exposure still requires transport, abuse protection, disclosure, audit and operational
review. Uniform errors alone do not provide timing uniformity or rate limiting.

## Privacy and compatibility

Never log/serialize credentials, evidence, snapshots, SQL parameters or object internals.
No new logs, spans, metrics, diagnostic registries, dependencies or settings are introduced.
The result has no fields or sensitive export. Existing process-memory/debugger limits remain.

No schema/index/migration changes; Alembic remains `0004_refresh_token_rotation`. Deployment
is additive internal code with no mounted route. Rollback removes this operation while retaining
all evidence and committed revocations. Never downgrade tables or undo revocation for rollback.
FL-013/014/015/016 contracts and ADR-0004/0005 statuses are unchanged. Phase 1 is incomplete.

## Validation and follow-up

Require unit proof-before-effect, malformed/forged input, lifecycle and privacy tests. Real
PostgreSQL tests cover scoped durability, repeats, refresh/reuse races in both forced orders,
rollback, commit rejection, cancellation, simulated lost acknowledgement/cleanup failure and
progress while an account writer holds its lock. Preserve all existing regression gates.
Independent Phase C review must assess authority, CAS failures and privacy. Identity/security
owners still must resolve authentication provenance, public logout semantics, audit/retention,
account-wide response and future access-token revocation latency before dependent exposure.

References: [Identity contracts](../IDENTITY.md), [security](../SECURITY.md),
[testing](../TESTING.md), [database](../DATABASE.md),
[ADR-0004](0004-refresh-token-protocol.md),
[ADR-0005](0005-identity-account-eligibility-coordination.md).
