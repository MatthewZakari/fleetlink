# FL-007 observability and telemetry foundation

OpenTelemetry provides vendor-neutral tracing, W3C propagation and technical metrics.
This is an opt-in infrastructure foundation, implemented for independent review; it is
not a production monitoring deployment. [ADR-0002](ADR/0002-opentelemetry-foundation.md)
records the standard, ownership and privacy decision. No collector, dashboard, SaaS,
log exporter, alert policy or business analytics is installed.

## Architecture and ownership

`fleetlink.observability` uses the OpenTelemetry API/SDK directly. The application factory
remains the composition root. Each API lifespan owns its providers, exporters and resource;
each worker prefork child creates its own providers on `worker_process_init` and closes
them on `worker_process_shutdown`. No providers, propagators or instrumentors are registered
globally. Repeated factories/lifespans can coexist. Construction and imports perform no
network I/O; exporter threads start only during enabled lifespan/child initialization or
the synchronous producer's first publication. A producer owns its telemetry unless injected.
Injected providers/exporters/readers allow deterministic testing without a collector.

Resource attributes are `service.name`, installed package `service.version` when available,
and `deployment.environment.name`. API and technical producer use `fleetlink-api`; worker
children use `fleetlink-worker`. Version currently resolves to `0.1.0`; absent package
metadata omits version without preventing startup. Environment comes
from existing Settings. No automatic resource detectors, host, customer, cluster or cloud
metadata are used. JSON logging retains its existing API/worker service identities.

Telemetry disabled creates no exporters, SDK providers, export threads or connections.
Application logging and correlation still work. Readiness never depends on a collector;
health/readiness bodies, Problem schemas, security headers and OpenAPI remain compatible.

## Configuration and local development

Immutable Settings reads the existing `FLEETLINK_` environment convention once. No dotenv
discovery occurs. All variables below have that prefix:

| Suffix | Default | Contract |
| --- | --- | --- |
| `TELEMETRY_ENABLED` | `false` | Explicitly enable trace and metric OTLP export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | Required when enabled; HTTP(S) origin, optional port 1–65535; no credentials, query, fragment or signal/path prefix |
| `OTEL_EXPORT_TIMEOUT_SECONDS` | `2` | Finite, greater than 0 and at most 10; export and per-provider shutdown budget |
| `OTEL_SAMPLE_RATIO` | `1` | Finite root trace ratio in [0, 1]; parent sampling decision is preserved |

Endpoint configuration is secret-wrapped and excluded from repr even though credentials
are rejected. Validation messages omit rejected inputs. Never serialize raw Pydantic
error dictionaries: they can contain input values despite safe formatted messages.
Names are derived from process roles instead of introducing unused service-name settings.

With an independently operated, trusted collector already available, an explicit local
invocation is:

```sh
FLEETLINK_TELEMETRY_ENABLED=true \
FLEETLINK_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 \
make api-run
```

The origin gets `/v1/traces` and `/v1/metrics` appended. Transport is OTLP HTTP/protobuf,
uncompressed, with normal TLS certificate verification for HTTPS. No telemetry listener
or `/metrics` endpoint is added. Use the same opt-in configuration with `make api-worker`
and `make api-task-smoke`, also explicitly enabling Celery. No collector is necessary for
tests. No local stack changes or heavier `infra-up` are introduced.

Enabled telemetry rejects all native `OTEL_*` environment overrides with a generic error.
SDK/exporter fallbacks could otherwise import credential providers, capture attributes,
override exporter headers or alter resource/ownership policies. Use FleetLink settings.
The same guard runs immediately before exporter creation to reject late native overrides,
without reloading the immutable FleetLink settings.
Do not launch with zero-code instrumentation. Collector authentication, mTLS, custom CA,
proxy configuration and environment override support require separate reviewed work.
Plain HTTP is appropriate only on a trusted private development path; production egress,
TLS, access, residency and retention need an operational design before enabling export.

## HTTP tracing and logging

Body-transparent pure ASGI middleware creates one SERVER span per request, with no extra
send/receive spans. It wraps the existing request context middleware so application and
completion logs run within the active span. W3C `traceparent` is extracted from exactly one
header with a bounded length. Invalid, duplicate or absent context creates an independent
trace. `tracestate` and baggage are intentionally not propagated or captured; this limits
vendor and untrusted metadata. Incoming trace IDs do not establish identity or authorization.

Span names become `METHOD route-template` after routing, such as `GET /items/{identifier}`.
Unmatched paths all use `unmatched`; raw URLs, queries and identifiers are never captured.
Known HTTP methods are allowlisted; arbitrary methods use `_OTHER`. Span attributes are
only method, route template and numeric response status. 5xx and escaped failures set ERROR
without descriptions or exception events. Cancellation still resets tracing/correlation
contexts. No request/response body or HTTP header capture is enabled.

`/health` and `/ready` are excluded from server spans to reduce probe noise, regardless of
incoming trace context. They still contribute to technical HTTP metrics. Existing logging
and correlation behavior is unchanged. Logs within any valid active OpenTelemetry span
add lowercase zero-padded hexadecimal `trace_id` (32 characters) and `span_id` (16).
Unsampled valid spans can still correlate logs; logs without a span omit these fields.
`correlation_id` remains independent and follows its existing validated HTTP/probe contract.
No baggage or arbitrary span attributes are copied into logs. Context variables preserve
concurrent request isolation and request-local log thresholds.

## Celery propagation

The synchronous `TechnicalProducer.publish` creates a PRODUCER span `celery.publish` and
injects standard W3C `traceparent` into Celery transport headers. No field is added to the
strict probe payload. The registered task extracts headers into a fresh context and creates
a CONSUMER span `celery.process`, including invalid-payload processing. Both spans carry
only the fixed task name and `messaging.system=rabbitmq`. Probe IDs/task IDs, queue UUID
suffixes, broker addresses/credentials, payloads and exception messages are not attributes.

Celery's existing retry preserves original headers. Each attempt has a distinct processing
span under the original producer trace/context; retry publication is not separately traced.
Absent or malformed context creates an independent valid processing trace. Worker logs
retain validated probe UUID correlation independently. Retry count/delays, acknowledgements,
result schema, bounded RabbitMQ RPC completion and delivery semantics are unchanged.
See [ADR-0001](ADR/0001-technical-task-completion.md), whose proposed status is unchanged.
Publishing through arbitrary `app.send_task` bypasses the explicit producer instrumentation;
it remains supported for the existing negative integration fixture, not a new application API.

## Database and Redis policy

SQLAlchemy's supported per-engine events on `AsyncEngine.sync_engine` create CLIENT spans
`postgresql.query` around cursor execution. Only `db.system.name=postgresql` is captured.
Statement text, literals, parameters, table names, connection URLs and database/user/host
identifiers are not captured at all. Failed executions set ERROR with no exception event.
Listeners are attached only to the lifespan-owned engine and removed during cleanup.
Transaction, session, commit, rollback, migration and pool ownership stay unchanged.
Connection establishment, pool pre-ping and migrations are not instrumented by this adapter.

Redis adapter methods create CLIENT spans `redis.PING`, `redis.SET`, `redis.GET`, `redis.DEL`.
Only `db.system.name=redis` is captured. Arbitrary keys, UUID suffixes, values, TTLs,
credentials, URLs and Redis exception messages are omitted. Existing operation/connection
deadlines, mandatory TTL, safe errors and cancellation semantics are preserved. Redis
cleanup is not traced. No dependency metrics duplicate these operation spans.

Contrib SQLAlchemy supports async sync-engine instrumentation, but its default query/URL
capture is broader than this boundary. Contrib Redis captures command/key statements and
wraps clients; Celery installs process-wide signal instrumentation; FastAPI/ASGI includes
URL/exception capture. Narrow native events/adapter/middleware integration avoids extra
packages, singleton instrumentation state and post-capture redaction. There is no outbound
HTTP client in the application; no speculative HTTP instrumentation dependency is added.

## Metrics and cardinality audit

Histograms supply counts through their count field; no duplicate request/task counter is
needed. Metrics are cumulative and independent of trace sampling. Periodic export is every
60 seconds; shutdown attempts final collection. These are technical measurements only.

| Name | Unit | Description | Attribute allowlist |
| --- | --- | --- | --- |
| `http.server.request.duration` | `s` | HTTP server request duration | `http.request.method`: 9 fixed methods or `_OTHER`; `http.route`: registered template or `unmatched`; `http.response.status_class`: response class |
| `fleetlink.celery.task.duration` | `s` | Technical Celery attempt duration | `celery.task.name`: sole registered probe name; `outcome`: `success`, `retry`, `failure` |

These are deliberately narrow custom integrations; the HTTP metric name/unit follow the
standard convention. SDK Views independently restrict both metric attribute key sets.
Every recorded value comes from bounded routing, fixed task registration or a fixed outcome.
Route definitions are trusted source, never dynamically generated from IDs. Histogram bucket
policy currently uses SDK defaults; capacity tuning and backend aggregation need later review.
SDK exemplars may refer to a sampled trace/span, but those references are not metric dimensions.

Custom span attribute audit: HTTP method/template/status; fixed task name/system;
fixed PostgreSQL/Redis system only. No user, correlation, trace, request, task ID, raw URL,
exception message, SQL text, Redis key, credential or arbitrary customer value is a metric
dimension. Resource service/environment/version change only with deployments. Future metrics
must document units, descriptions, labels and bounds before adoption.

## Failure, shutdown and operational limits

Export happens outside requests/tasks using SDK batch/periodic workers. Trace queue is
512 spans, batches at most 128, scheduled every 5 seconds; saturation can drop telemetry.
OTLP retries use upstream bounded backoff within the configured export timeout (up to six
attempts, including upstream connection recovery behavior). Telemetry is best effort;
there is no disk queue, delivery guarantee or retrying business operation on export failure.

Safe exporter wrappers isolate exporter exceptions and emit only `telemetry_export_failed`,
at most once per wrapper per minute. Specific SDK exporter/processor logger filters replace
sensitive diagnostics with `telemetry_runtime_event`, preserving severity and limiting each
namespace to one per minute, routed through the existing FleetLink JSON sink. Raw
endpoint/error/response text and traceback are removed;
unrelated library warnings and RabbitMQ diagnostics are not suppressed. The filters are an
idempotent logging privacy boundary, not global provider state. They deliberately trade
diagnostic detail for privacy; operators investigate the collector independently.

API cleanup is cancellation-shielded and runs blocking telemetry shutdown off the ASGI loop.
Trace shutdown drains pending spans within its configured processor deadline, then closes
the exporter. SDK 1.45's public batch shutdown hardcodes 30 seconds and its force-flush
timeout is not enforced; one documented subclass hook delegates to the SDK batch engine
with FleetLink's deadline. Revalidate that private hook on upgrades. Metric reader shutdown
has a configured join deadline and the exporter has its own bounded network timeout. Each
provider gets a budget, not one hard wall-clock bound for the whole lifespan. Shutdown
budgets have a 1 ms floor to preserve positive SDK deadlines. DNS/OS stalls
can outlast library timeouts; a collector that slowly drips a response is not a verified
hard-deadline case. Pending telemetry can be lost when a deadline expires.

Warm worker shutdown runs child cleanup; abrupt kill cannot flush. Injected test exporters
must obey their own shutdown contract. The foundation does not add helper threads to hide
blocking exporters, infinite retries, telemetry readiness probes, commits or Redis retries.
Runtime collector unavailability does not fail requests, tasks, transactions, readiness or
startup. Malformed local configuration still fails validation normally.

## Testing and rollback

`make api-test` stays infrastructure-free. `make api-test-observability` selects deterministic
in-memory, mocked OTLP, ASGI concurrency/body-transparency, sampling, propagation, safe-log,
metric cardinality, event-listener and shutdown tests. SQLite in one event-driver fixture
tests listener behavior only; it is not PostgreSQL validation. Existing CI already covers
all new source/tests via Ruff, format, strict mypy and pytest; no CI service is added.

`make api-test-db` additionally proves the privacy listener works with real async PostgreSQL
using only [the existing isolated FL-005 procedure](TESTING.md#fl-005-database-validation).
`make api-test-broker` additionally proves RabbitMQ/prefork trace continuation and retries
from the producer's in-memory span to worker JSON logs. That case deliberately enables worker
export to a refused loopback port and verifies successful completion and shutdown despite
collector failure. It creates/deletes only its UUID queue/exchange/reply resources.

No successful collector export, backend interoperability, production performance, security
scan, ARM/cloud compatibility or production readiness is established by these tests.
Future operational work includes approved collector topology/access, retention, sampling
abuse controls, load tests, objectives, dashboards and alerts. Parent-based sampling trusts
an upstream sampling decision; an edge trust policy is future work, not authorization here.

Rollback warm-stops workers, disables `TELEMETRY_ENABLED`, and restores previous code/lock
if needed. No schema migration, data conversion, queue change, volume reset or mobile change
is required. FL-006 temporary probes/results retain their original expiration/cleanup rules.

## Dependency evaluation

Researched upstream metadata/docs on 2026-09-30. Direct constraints follow the repository's
bounded range plus generated `uv.lock` policy; resolved versions are 1.45.0, Python >=3.10
upstream, tested here on Python 3.12. No unrelated package upgrade was requested.

| Package | Constraint; resolved | Purpose | License | Meaningful alternative / selection |
| --- | --- | --- | --- | --- |
| opentelemetry-api | `>=1.45,<1.46`; 1.45.0 | Standard spans, context, W3C propagator and metric interfaces | Apache-2.0 | Proprietary APIs reduce portability; handwritten propagation duplicates the standard |
| opentelemetry-sdk | `>=1.45,<1.46`; 1.45.0 | Local providers, parent ratio sampler, resources, processors, readers and memory exporters | Apache-2.0 | API-only cannot record/export; bespoke SDK duplicates lifecycle/sampling/aggregation |
| opentelemetry-exporter-otlp-proto-http | `>=1.45,<1.46`; 1.45.0 | Standard OTLP protobuf HTTP trace/metric export | Apache-2.0 | OTLP gRPC adds a native transport stack without need; console/file exporters lack collector delivery |

Keep API/SDK/exporter versions aligned. The SDK resolves semantic conventions 0.66b0;
exporter transport/common support also resolves 0.66b0, even though direct SDK/exporter
releases are stable. These support APIs and the batch shutdown hook need review on upgrades.
New transitive components include protobuf (BSD-3-Clause), googleapis-common-protos
(Apache-2.0), urllib3 (MIT), and OpenTelemetry proto/common/transport (Apache-2.0).
Costs are extra package maintenance, bounded buffers, SDK threads and explicit export egress.
Pinning prevents silent fixes as well as changes. No vulnerability scan is claimed.

Sources: [API metadata](https://pypi.org/project/opentelemetry-api/),
[SDK metadata](https://pypi.org/project/opentelemetry-sdk/),
[OTLP exporter metadata](https://pypi.org/project/opentelemetry-exporter-otlp-proto-http/),
[Python exporters](https://opentelemetry.io/docs/languages/python/exporters/),
[FastAPI instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html),
[SQLAlchemy instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/sqlalchemy/sqlalchemy.html),
[Redis instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/redis/redis.html),
[Celery lifecycle](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/celery/celery.html).
