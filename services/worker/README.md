# worker

FL-006 runs a dedicated Celery process from the shared modular-monolith package:
`FLEETLINK_CELERY_ENABLED=true make api-worker` at repository root. Its entry point is
`apps/api/src/fleetlink/worker.py`; no second package/configuration or microservice is added.
Only a harmless technical probe is registered. Domain use cases remain owned by backend
bounded contexts and no business tasks are implemented.

See [worker operations](../../docs/ASYNC_INFRASTRUCTURE.md) and
[real-service tests](../../docs/TESTING.md#fl-006-broker-validation).

FL-007 initializes OpenTelemetry per prefork child, with service identity `fleetlink-worker`.
Export defaults off; enable it separately from Celery using the shared immutable Settings.
Standard W3C transport headers continue producer traces, independently of probe UUID log
correlation. Collector failures do not change task/retry/result semantics. Warm child
shutdown attempts bounded telemetry cleanup; abrupt kills can lose pending spans.
See [telemetry configuration, privacy and lifecycle](../../docs/OBSERVABILITY.md).
