# Coding standards

## Shared rules
Favor clear, typed, cohesive code with one authoritative implementation for each domain rule. Keep modules aligned with context ownership and dependencies directed inward. Use domain services or validated configuration for policy; do not scatter fees, timeouts, eligibility or limits throughout code.
Use UUIDs, UTC, explicit money/quantity types and deliberate error mapping. Validate all external inputs including events, files, callbacks and stored offline operations. Do not swallow exceptions or log secrets.
Avoid speculative abstractions; inspect existing code first. Dependency additions require justification, license/security review and version management. Commit application lockfiles and reproducible build inputs when introduced.

## Backend direction
Python/FastAPI handlers should validate/map transport, call application use cases and map outcomes. Domain code must not depend on HTTP or ORM models. SQLAlchemy repositories implement typed ports; Alembic changes are reviewed separately from application behavior.
Use explicit transaction boundaries and dependency injection. Avoid blocking I/O inside async handlers; choose appropriate async adapters or bounded worker execution. Bound timeouts and retries, propagate cancellation and release resources.
Adopt formatter, linter and static type-checking configuration in a later tooling task; record actual commands in README.md when available.

## Mobile direction
Use Dart analysis and formatting, immutable Freezed models, Riverpod composition/state, and GoRouter navigation consistent with authorized context. UI route guards improve experience but never replace backend authorization.
Separate presentation, application/domain and data/platform adapters. Dio DTOs map to domain types. Keep Hive cache scoped by user and schema version; keep credentials in Flutter Secure Storage.
Model loading, empty, offline, pending, failed and confirmed states. Dispose subscriptions and background resources. Respect OS permission/lifecycle restrictions for location and messaging.
Use localization resources, semantic labels, accessible controls, scalable text and testable formatting. Never embed business rules in widgets or use device clocks as financial authority.

## Observability and reliability
Emit structured events with service/context, severity, UTC timestamp, correlation ID and safe operation identifiers. Redact credentials, payment details and precise location unless a narrowly approved diagnostic policy allows it.
Instrument latency, failures and saturation without unbounded user-ID metric labels. Critical jobs require idempotency, retry bounds, dead-letter visibility and reconciliation.
Treat logs as diagnostics and protected audit trails as accountability records with separate retention/access requirements.

## Reviews and compatibility
Keep pull requests focused; describe the problem, behavior, tests actually run and risks. Add tests for changed behavior, update contracts/docs and record significant decisions as ADRs.
Preserve API, event and persisted-data compatibility unless a breaking change is authorized. Review migration rollout and old-client behavior. Report known debt with owner/follow-up rather than concealing it.
