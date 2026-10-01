# ADR-0002 — Opt-in OpenTelemetry with explicit ownership and privacy boundaries

- Status: Proposed; implemented for FL-007 independent review, not milestone acceptance.
- Date: 2026-10-01.
- Owners/reviewers: FleetLink maintainers, implementer and independent reviewer.
- Related task: FL-007.

## Context and decision

The architecture calls for logs, traces and metrics, but has no telemetry standard or
export backend. FL-007 requires vendor-neutral export without a runtime collector dependency,
repeated testable factories, prefork safety and a telemetry exfiltration boundary.

Use OpenTelemetry API/SDK and optional OTLP HTTP/protobuf trace/metric export. Each API
lifespan and worker child owns providers; no global provider registration or zero-code
instrumentation. Initialize worker telemetry after fork. Use standard W3C traceparent
transport headers for HTTP/Celery, independently of correlation IDs. Do not propagate
tracestate/baggage in this foundation. Default export is disabled. Collector availability
does not determine readiness or business execution.

Use parent-based ratio sampling and minimal technical duration histograms. Allowlist bounded
attributes; never capture raw URLs, HTTP headers/bodies, SQL text/parameters, Redis keys/values,
broker credentials or arbitrary exception text. Prefer narrow native lifecycle integrations
over contrib defaults that capture more data or mutate global instrumentation. See
[the complete contract and dependency evaluation](../OBSERVABILITY.md).

## Alternatives

- Vendor SDKs couple application contracts/export to a provider not selected by FleetLink.
- API-only OpenTelemetry lacks recording, sampling and export facilities.
- Global/automatic instrumentation is convenient but weakens instance ownership and privacy.
- Contrib HTTP/SQLAlchemy/Redis/Celery instrumentation can be revisited with a proven safe
  capture policy; its wider defaults and singleton hooks are unnecessary for this narrow scope.
- OTLP gRPC adds transport dependencies without a current need; a full monitoring stack
  adds operational infrastructure outside this task.
- Always-on export makes an auxiliary network boundary implicit; tail sampling requires
  collector/storage infrastructure that has not been selected.

## Consequences, compatibility and data impact

Standard propagation/export preserves future backend choice. Local providers and memory
exporters keep tests deterministic. Costs include dependencies, SDK threads/buffers and
best-effort telemetry loss on saturation/shutdown. Narrow integrations require maintenance;
the pinned SDK shutdown hook must be revalidated on upgrade. Parent-based sampling needs
an ingress trust/abuse policy before production. Reject native OTEL overrides while enabled
to keep configuration, capture and credential policy explicit.

No business schema, task payload, transaction, result-backend, acknowledgement, probe,
security header or correlation contract changes. ADR-0001 remains proposed and unchanged.
Rollback disables telemetry, warm-stops workers and restores code/lock; no data migration
or infrastructure reset is needed.

## Validation and follow-up

Require infrastructure-free tests for propagation, logging/concurrency isolation, sampling,
metrics cardinality, privacy, failure isolation and bounded cleanup. Reuse isolated FL-005
database tests and FL-006 broker/worker lifecycle tests for real-service evidence.
Successful collector/backend interoperability, capacity, residency, retention, secure egress,
authentication, load tests, dashboards/alerts and deployment remain future work owned by
maintainers. Review this decision before enabling production export or upgrading the SDK.
