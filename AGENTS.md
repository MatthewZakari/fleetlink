# FleetLink agent instructions

## Scope and authority
FleetLink is production commerce and logistics software. Accepted FL-001 through FL-003 established the engineering specification, monorepo bootstrap, technical API endpoints, minimal Flutter shell, developer tooling and optional local infrastructure. Implementation is authorized only by the current explicitly scoped engineering task; FL-004 strengthened the FastAPI technical core; FL-005 adds optional async PostgreSQL engine/session infrastructure, Alembic revision tracking and isolated PostgreSQL/PostGIS tests. Those tasks authorized no domain tables, authentication, marketplace, payments or logistics implementation.
FL-006 adds opt-in async Redis, RabbitMQ-backed Celery technical probes, dedicated workers and explicit isolated real-service tests. It awaits independent review; implementation does not imply acceptance. No domain jobs, outbox or scheduler is authorized.
FL-007 adds opt-in OpenTelemetry tracing/metrics, W3C HTTP/Celery propagation and trace/log correlation with explicit lifecycle ownership and bounded privacy-safe attributes. It awaits independent review; no collector/platform deployment or business analytics is authorized by FL-007. See docs/OBSERVABILITY.md.
FL-008 adds classified secret configuration, an environment snapshot source and controlled diagnostic redaction/CI scanning, pending independent review. It authorizes no identity, provider integration or FL-009 work. See docs/SECRETS.md.
FL-009 introduces the Identity domain/persistence foundation for canonical UUID users, account status and multiple platform roles, pending independent review. Identity owns `identity_users` and `identity_user_roles`; role labels grant no resource authorization and create no Commerce/Logistics profiles. Authentication, credentials, sessions, memberships, HTTP identity endpoints and outbox remain deferred. See docs/IDENTITY.md.
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
