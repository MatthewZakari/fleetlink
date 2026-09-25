# FleetLink agent instructions

## Scope and authority
FleetLink is production commerce and logistics software. FL-001 establishes the engineering specification only: no application scaffolding, database tables, authentication, marketplace, payments, or logistics implementation.
The accepted technology and architecture direction is documented in docs/ARCHITECTURE.md. Planned capabilities are not implemented capabilities.
Read this file before making any change, then README.md and the relevant documents under docs/. Inspect any more specific AGENTS.md instructions in the area being changed.

## Required working process
1. Read relevant architecture, product, API, database, security, and testing documentation before design or implementation.
2. Inspect existing implementations and tests before creating abstractions. Search for reusable functionality; never duplicate business logic.
3. Respect bounded-context ownership. Use explicit application interfaces or versioned events; do not reach into another context's repositories or tables.
4. Keep changes within the requested task. Avoid unrelated refactors and premature microservices.
5. Justify every new dependency: purpose, alternatives, maintenance, license, security, and operational impact.
6. Add tests for new behavior and bug fixes. Run relevant tests and checks before declaring work complete.
7. Never claim a test passed unless it was actually executed successfully. Report exact commands and distinguish passed, failed, skipped, and unavailable checks.
8. Never commit credentials, tokens, private keys, customer data, or secrets, including in examples and logs.
9. Update documentation when behavior or architecture changes. Record significant architectural decisions using docs/ADR/README.md.
10. Preserve backwards compatibility unless the user explicitly authorizes a breaking change. Document migration and rollback implications.
11. Report unresolved warnings, failures, risks, and technical debt. Do not silently suppress checks.

## Engineering invariants
- Authenticated identity belongs to a user. A user may hold multiple roles; switching role never requires another account and never grants unassigned privileges.
- Public marketplace reads may be anonymous; mutations and private data require explicit authorization.
- Use UUID identifiers, timezone-aware UTC timestamps, typed interfaces, external-input validation, structured logs, consistent errors, and correlation IDs.
- Business policy belongs in domain services or validated, versioned configuration, not scattered hardcoded values.
- Domain rules have one authoritative owner. Dependency injection connects infrastructure to application ports; domain code does not depend on transport or persistence frameworks.
- Design state-changing operations for idempotency and concurrency. Reliability-critical domain events require a transactional outbox and deduplicating consumers.
- Financial entries are balanced, immutable double-entry postings. Never use floating-point money or client-owned wallet balances.
- Consider security, observability, accessibility, localization, offline reconciliation, and horizontal scaling in each relevant change.
- Document public APIs. Add an ADR before adopting significant architectural deviations.

## Completion
Review the diff for unintended files and secrets. Summarize changes, validation actually performed, limitations, and follow-up work. Documentation-only changes require structure, link, and consistency checks; do not invent application test results.
