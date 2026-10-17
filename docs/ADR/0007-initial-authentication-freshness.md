# ADR-0007 — Initial authentication assertion freshness

- Status: Proposed (FL-018 Phase B authorized; independent Phase C review pending).
- Date: 2026-10-16.
- Owners/reviewers: Identity maintainers and independent security reviewer.
- Related task: FL-018; preserves FL-009 through FL-017.

## Context

FL-015 checks assertion chronology but deliberately imposes no maximum assertion age.
Authentication provenance, assurance and production policy remain unresolved. FL-018 adds
one opt-in admission check without changing the existing establishment contract.

## Decision

Add Identity application `AuthenticationFreshnessPolicy(max_assertion_age)` with an explicit
positive timedelta and no default. `establish_fresh_initial_session` requires this policy
and the existing establishment inputs. Normalize aware instants to UTC and require:

```text
0 <= created_at - authenticated_at < max_assertion_age
```

Zero age is accepted; exact maximum age and older assertions raise fixed-message
`AuthenticationAssertionStale`. Future assertions and invalid types/naive timestamps raise
fixed-message ValueError, consistent with existing invalid chronology conventions. Reject
before repository access, credential generation or mutation. Successful admission delegates
exactly once to unchanged `establish_initial_session`; no establishment logic is duplicated.
The application depends only on standard-library and Identity inner-layer types.

## Security and transaction boundaries

Freshness is not provenance: the assertion and timestamps remain trusted caller data.
Forged authentication timestamps are not detected; no assurance level or single-use proof
is established. Policy and operation time must never come from client-controlled input.
The old entry point remains unchanged, so enforcement is opt-in, not universal. Future
public authentication requires separately reviewed, provenance-verified composition.

The evaluation instant is explicitly supplied `created_at`, not a hidden clock. There is
no freshness guarantee at lock acquisition, commit or response delivery. Repeated use of
an assertion within its window remains possible; no assertion consumption or quota is added.

Existing account -> session -> token ordering, coordinated eligibility, optimistic CAS,
absolute refresh expiry, possession verification and session-scoped revocation are unchanged.
Use the existing caller transaction and FL-014 runner; every failure escapes without retry.
Export provisional credentials only after commit AND cleanup. Commit uncertainty or
post-commit cleanup/delivery failure can leave durable rows without a returned credential.
Never log assertions, credentials, verifier material, snapshots or SQL parameters. No
HTTP error mapping, diagnostic capture or generated-secret registry is introduced.

## Alternatives

- Changing FL-015 would break existing callers and make freshness mandatory without authority.
- An optional policy on FL-015 makes the distinct opt-in guarantee less explicit.
- A standalone validator leaves composition to each caller; the thin entry point binds the
  check to establishment without duplicating business rules.
- A system clock or post-lock recheck changes operation-time semantics and lock behavior.
- Provider integration, JWTs and assertion consumption require separate architecture/policy.

## Compatibility, rollout and rollback

No tables, indexes, migrations, dependencies, lockfile changes, runtime settings, defaults,
public routes or transaction-runner changes. Alembic remains `0004_refresh_token_rotation`.
Adopt the new entry point explicitly in reviewed internal composition. Legacy callers retain
their behavior. Rollback removes the additive entry point and its callers while preserving all
records and revocations; never downgrade data or silently replace a freshness-dependent caller
with legacy establishment. Production authentication is not enabled; Phase 1 remains incomplete.
ADR-0004/0005/0006 remain Proposed.

## Validation and follow-up

Unit tests cover age boundaries, types, UTC equivalence, no-I/O rejection, exact delegation,
legacy compatibility and safe errors. Reuse PostgreSQL durability/failure and account-lock
scenarios for both entry points; preserve complete migration, CAS, privacy and regression gates.
See [testing](../TESTING.md#fl-018-assertion-freshness-validation). Tests are not independent
security acceptance. Identity/security owners must decide production ages, provenance,
assurance, public transport and any stronger freshness or replay policy before activation.
See [Identity](../IDENTITY.md#fl-018-opt-in-assertion-freshness) and [security](../SECURITY.md).
