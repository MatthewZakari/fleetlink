# ADR-0003 — Secret configuration source and diagnostic boundaries

- Status: Proposed (FL-008 independent review pending).
- Date: 2026-10-01.
- Owners/reviewers: Platform maintainers; security review required.
- Related task: FL-008.

## Context

Platform clients already use frozen Settings, SecretStr and sanitized transport errors.
Pydantic error strings hide inputs but structured errors can retain them. Arbitrary logging
messages and exception headers also create uncontrolled diagnostic surfaces. Future providers
must not require changing consumers or implicit network/filesystem access during construction.

## Decision

Reuse existing settings and dependencies. Explicitly classify current credentials and the
sensitive OTLP origin. Add a synchronous SecretSource protocol and environment snapshot adapter,
injected at settings construction. Add scoped immutable redaction and structural allowlists at
FleetLink diagnostic boundaries. Resource lifetime remains explicit and captures a snapshot.
Use standalone MIT Gitleaks 8.30.1 with a pinned archive checksum, full Git history and working
tree scanning, redacted output and no broad exclusions.

## Alternatives

Global mutable secret registries risk cross-application contamination and retained credentials.
Global library monkey patches obscure ownership. A managed-provider SDK or generic refresh
framework introduces dependencies and operations outside FL-008. Repository-native regular
expressions have weaker provider-pattern coverage and impose maintenance. A hosted scanner
adds external account/data dependencies. The standalone CLI avoids the separate Gitleaks action
licensing model while retaining upstream rules and Git support.

## Consequences

Secrets remain plaintext in trusted process/client memory. Scoped redaction protects tested
raw/URL-encoded values, not arbitrary transformations. Logs become less descriptive for unknown
events and exception types. Scanner updates need reviewed version/checksum changes. No Python
dependency is added. Gitleaks upstream maintenance must be monitored; security fixes and scanner
rule coverage are review triggers. Direct SDK diagnostics remain outside controlled wrappers.

## Compatibility and data impact

No variable rename, schema change, identity functionality or dotenv discovery. Local/test
placeholders remain; enabled staging/production resources require explicit credentials.
Seven-field Problem responses remain; arbitrary exception headers are restricted. Rotation
requires drained resource recreation/restart. Rollback is a reviewed revert and restart.

## Validation and follow-up

Dedicated infrastructure-free sentinel tests, all API/observability checks, strict typing,
existing real PostgreSQL/PostGIS/Redis/RabbitMQ/Celery flows and Flutter regression checks are
required. Independent review must evaluate diagnostic boundaries and scanner maintenance.
Future providers need explicit safe resolution, version/lease semantics and replacement ownership.
See [operations and limitations](../SECRETS.md).
