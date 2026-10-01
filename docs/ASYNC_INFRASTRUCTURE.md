# FL-006 Redis, RabbitMQ and Celery foundation

This is technical infrastructure only. Redis holds ephemeral probe keys; RabbitMQ is the
only task broker; Celery runs a harmless probe in a dedicated process. PostgreSQL remains
the durable data direction. No domain workflows, outbox, scheduler or deployment exist.
API `/health` and `/ready` contracts remain unchanged: `dependency_checks=not_configured`
does not assert external dependency health.

## Configuration

All settings use the existing immutable `Settings` and `FLEETLINK_` prefix. No dotenv
discovery occurs. Compose reads `.env` through its existing script; Python commands need
exported variables or `uv run --env-file .env --project apps/api --locked …`. RabbitMQ
credentials and published AMQP port reuse the existing Compose variables. Never print
settings, broker URLs, client objects or raw validation errors.

| Suffix | Default | Meaning / limits |
| --- | --- | --- |
| `REDIS_ENABLED` | `false` | Enable per-API-lifespan resources; no startup PING |
| `REDIS_HOST` / `REDIS_PORT` | `127.0.0.1` / `6379` | Reachable host; port 1–65535 |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | unset | Optional ACL credentials, hidden from repr |
| `REDIS_MAX_CONNECTIONS` | `5` | Per-instance limit, 1–50; saturation fails promptly |
| `REDIS_CONNECT_TIMEOUT` | `2` | Connect seconds, greater than 0 and at most 30 |
| `REDIS_OPERATION_TIMEOUT` | `2` | Socket and whole-operation seconds, same bounds |
| `CELERY_ENABLED` | `false` | Explicit opt-in to producer/worker resources |
| `RABBITMQ_HOST` | `127.0.0.1` | Reachable host; IPv6 is bracketed in URL |
| `RABBITMQ_AMQP_PORT` | `5672` | Existing Compose port, 1–65535 |
| `RABBITMQ_USER` / `RABBITMQ_PASSWORD` | existing development placeholders | Hidden from repr; override outside disposable local use |
| `RABBITMQ_VHOST` | `/` | Existing local vhost; credentials and vhost are separately encoded |
| `BROKER_CONNECT_TIMEOUT` | `2` | TCP/AMQP connect seconds, greater than 0 and at most 30 |
| `BROKER_OPERATION_TIMEOUT` | `2` | Socket read/write and publish-confirm seconds, same bounds |
| `CELERY_QUEUE` | `fleetlink.technical.v1` | Only this name or `.fl006.<32 lowercase UUID hex digits>` suffix |
| `CELERY_CONCURRENCY` | `1` | Prefork processes, 1–8; prefetch multiplier 1 |

NaN, infinity and invalid ranges fail validation. Settings are read once; there is no
second configuration module. Factory construction performs no network I/O. Celery never
starts inside FastAPI, even with `CELERY_ENABLED=true`.
Native `CELERY_BROKER_*`, `CELERY_RESULT_BACKEND`, `CELERY_CONFIG_MODULE` and `CELERY_LOADER`
overrides are rejected so they cannot silently replace RabbitMQ or bypass Settings.

## Redis lifecycle and operations

`TechnicalRedis` in `infrastructure/redis.py` creates a lazy, instance-owned client/pool.
`get_redis` exposes enabled resources through native DI. Lifespan closes acquired resources
on shutdown or partial startup failure. Cleanup is shielded from AnyIO cancellation and
bounded; cancellation during work propagates. Connectivity/authentication/timeouts become
sanitized `RedisError`; programming and Redis command errors propagate separately.

The narrow interface is `ping`, `write(UUID, value, ttl_seconds=60)`, `read`, `delete`,
and `close`. Keys are `fleetlink:technical:fl006:<UUID>`. Values are strings of at most
256 UTF-8 bytes; TTL is mandatory (1–300 seconds). Only exact keys can be deleted; there
is no scan, wildcard deletion or flush. Automatic retries are disabled. Connection budget
is `REDIS_MAX_CONNECTIONS × API processes`. Redis is never authoritative for orders,
inventory, balances, payments or task completion history.

The accepted local Redis service has no password. Supplying a password alone does not
enable server authentication. Set credentials against an ACL-configured server. The negative
integration test uses a nonexistent UUID-named ACL user without creating/modifying users.
Positive authenticated access is exercised when the selected server requires configured
credentials; local defaults establish configured access.

## Worker and producer commands

Run `make infra-up`. In separate terminals with matching host, credentials, vhost and queue:

```sh
FLEETLINK_CELERY_ENABLED=true make api-worker
FLEETLINK_CELERY_ENABLED=true make api-task-smoke
```

To load custom root configuration explicitly:

```sh
uv run --env-file .env --project apps/api --locked env FLEETLINK_CELERY_ENABLED=true \
  python -m fleetlink.worker
uv run --env-file .env --project apps/api --locked env FLEETLINK_CELERY_ENABLED=true \
  python -m fleetlink.technical_smoke
```

`fleetlink.worker` constructs its app only in `main`. Use this entry point: it disables
the credential-bearing startup banner and reduces library logs to structured event names,
severity and safe exception classes. Task logs correlate using the validated probe UUID.
No full payload, URL, username, password or raw traceback is logged. Linux prefork workers
use UTC and JSON-only tasks/results. Remote control, gossip, mingle, events and beat are
disabled. No Celery canvas/built-in task remains registered.

`TechnicalProducer` is synchronous and single-thread owned. Run it in a dedicated command
or process, never on an ASGI event loop. It owns a two-connection pool; closing one producer
does not close another's pool. Worker broker pools also have a limit of two per process,
in addition to consumer/heartbeat and transient publishing connections. Prefork children
recycle after 100 tasks. This is not a production capacity budget or concurrency benchmark.

The smoke command distinguishes `technical_publish_confirmed` (broker acknowledgement)
from `technical_completion_observed` (independently read terminal worker result). Publication,
acceptance, consumption and success are different facts. Publishing can succeed without a
worker; successful publication is not evidence of completion.

## Task, queue and result semantics

Only `fleetlink.technical.probe.v1` is registered. Its strict payload contains UUID string
`probe_id`, integer `value` (-1000–1000), `failures_before_success` (0–3, default 0), and
`reject` (boolean, default false). Extra fields/coercions are rejected at the worker. It
returns `{probe_id, value: value * 2, attempts}`, with no database/domain mutation. Use only
synthetic input. Two retries are permitted, delayed 1 then 2 seconds. Jitter is disabled
for this deterministic demonstration. Setting failures to 2 succeeds on attempt 3; setting
3 raises `ProbeExhausted` on attempt 3. Rejection raises `InvalidProbe` on attempt 1.
Validation/programming errors are not retried. Exhaustion is visible in the RPC failure
and `technical_task_failed` log. This is not a future business retry policy.

Task queue/direct exchange names equal `CELERY_QUEUE`. Tasks are persistent and queue
metadata durable. Backlog is limited to 100 messages / 256 KiB, messages expire after
60 seconds, and overflow rejects publishing. Arbitrary queue creation is disabled.
Late acknowledgements are enabled; terminal failures/timeouts are acknowledged. Unexpected
child loss is not automatically requeued, preventing poison loops. Whole worker/connection
failure may redeliver unacknowledged messages. Duplicates and lost observations remain
possible; exactly-once execution is not provided. No dead-letter routing is configured.
Domain owners must design dead-letter ownership, replay, deduplication and reconciliation
in separately approved work.

The result backend is a narrow Celery RPC subclass on RabbitMQ. Each producer thread gets
one namespaced reply queue, at most 32 messages / 64 KiB. Result messages are transient
JSON with a 60-second TTL; unused queues expire after 60 seconds and producer close deletes
its own reply queue. Queue metadata is durable to satisfy RabbitMQ 4.3; transient results
are still lost on broker restart. There is no Redis backend or durable task ledger. Results
are for the originating producer, not cross-process lookup/audit. Expiry, overflow, consumer
loss or restart can make successful execution unobservable.

The subclass disables implicit reply-declaration/result-publication retries. Completion
polls Celery RPC metadata and terminal cache every at most 50 ms, avoiding Celery 5.6.3's
async consumer reconnect on ordinary socket timeouts. Completion deadline defaults to
20 seconds (maximum 60); an in-flight bounded broker operation can overrun it. Connect,
read/write and confirm limits are per phase, not one hard deadline across DNS, declaration
and publishing. DNS scheduling/OS behavior remain environmental limits; integration has
an outer 180-second deadline. Publication is not automatically retried: a timeout can mean
acceptance with a lost acknowledgement. Reconcile before retrying future mutations. A wait
timeout does not cancel the task.

## Shutdown, recovery and limitations

SIGTERM requests warm shutdown: stop consuming and finish active work. The task has a
5-second soft / 10-second hard execution limit; Celery's soft cold-shutdown phase is
10 seconds. Tests allow 20 seconds for TERM before reporting failure and killing only
their worker. SIGQUIT requests cold shutdown; SIGKILL prevents cleanup and can cause loss
or redelivery. Cancellation cannot undo effects; these probes have none. Connection
startup/recovery stops after three retries. After restoring connectivity, restart a worker;
eligible queued probes can execute before their 60-second expiry. Failed retry or result
publication can lose completion observations, even after execution.

RabbitMQ 4.3 reports a `global_qos` deprecation diagnostic from Celery's classic-queue path.
Prefetch and real consumption work with the accepted image. Do not suppress this warning
or enable deprecated features to hide it. Revalidate before a RabbitMQ upgrade removes
that capability. Quorum/native delayed delivery is not enabled; it adds topology beyond
this foundation. The existing management metrics deprecation is also an upstream warning.
Production TLS, ACL provisioning, HA, monitoring, rate limits and replay remain future work.
Keep current development credentials and unencrypted services on the private local network.

## Validation, cleanup and rollback

See [test procedure](TESTING.md#fl-006-broker-validation). Tests use fresh UUID resources,
never purge shared queues, flush Redis or reset volumes. They start/stop their own prefork
workers. Normal cleanup removes exact resources; keys/reply queues also expire. A hard-killed
suite can leave an empty durable UUID task queue/exchange after messages expire. Inspect and
remove only that exact recorded test resource after verifying no consumers. No broad cleanup
target is provided. Test cleanup never removes the standard development task queue.

Rollback stops producers, warm-stops workers, disables opt-in flags and restores prior code
and lock. Allow technical messages/results/keys to expire; optionally remove only reviewed
FL-006 queue/exchange names. No SQL migration, domain conversion, volume reset or FL-005 test
database change is required. Existing API probes and database behavior remain compatible.

## Dependencies and maintenance

uv generated the Python 3.12 lock graph; no resolved lock entries were hand-edited.

| Direct dependency | Constraint; resolved | License | Purpose and alternatives |
| --- | --- | --- | --- |
| redis | `>=8.1,<9`; 8.1.0 | MIT | Maintained redis-py asyncio client/pool; standalone aioredis duplicates functionality, sync-only Redis blocks ASGI |
| celery | `>=5.6.3,<5.7`; 5.6.3 | BSD-3-Clause | Selected task/worker runtime; raw AMQP would require reimplementing worker execution/retries; RQ/Dramatiq or orchestration platforms change the stack |
| kombu | `>=5.6,<5.7`; 5.6.2 | BSD-3-Clause | Celery's existing AMQP runtime, direct because source imports its queue/connection/producer APIs; another AMQP client duplicates transport |
| celery-types (dev) | `>=0.24,<1`; 0.26.0 | Apache-2.0 | Strict mypy stubs for Celery/Kombu; blanket import ignores remove validation; not required at runtime |

All support Python 3.12. Celery brings py-amqp, billiard (prefork), vine and CLI/timezone
support transitively. No Redis Celery extra, alternate broker or compiled Redis parser is
added. Review advisories, licenses and lock diffs on upgrades; tests are not vulnerability
scans. Pinning also prevents automatic security fixes. Revalidate the RPC subclass, producer
pool cache hook and stub/runtime differences on upgrades. The hook replaces only a producer
instance's `_pool`, never resets Kombu global pools. Operational costs include worker CPU,
memory and bounded Redis/broker resources.

Sources: [Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html),
[Celery tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[RabbitMQ queues](https://www.rabbitmq.com/docs/queues),
[redis-py asyncio](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html),
[Celery typing project](https://github.com/sbdchd/celery-types).
See [ADR-0001](ADR/0001-technical-task-completion.md) for the result-backend decision.

## FL-007 telemetry integration

The explicit producer injects W3C traceparent transport headers; the technical task extracts
context and records one processing span/duration per attempt. Probe payload/result schemas,
UUID log correlation, retry delays/budget, acknowledgements, bounded RPC result architecture
and all queue settings remain unchanged. Each prefork child owns telemetry initialized
after fork and closed on child shutdown. Export is separately opt-in and defaults off;
collector failure is auxiliary and cannot fail a task or become a readiness dependency.
No broker URL, queue UUID suffix, payload or task ID becomes a metric/span dimension.
See [observability](OBSERVABILITY.md) and the unchanged [ADR-0001](ADR/0001-technical-task-completion.md).

## FL-008 credentials and rotation

Existing Redis ACL and RabbitMQ credential variable names are preserved and typed as
`SecretStr`. Secret-source resolution snapshots configuration once; no provider lookup,
background refresh or worker polling is introduced. Enabled staging/production broker clients
reject development defaults. Empty enabled RabbitMQ credentials fail with a field-specific
sanitized diagnostic. Broker connection setup and producer/worker cleanup suppress known
transport messages; worker library diagnostics retain the structural JSON boundary.
Rotate by draining and recreating clients/workers with new settings, then retiring old
credentials. RabbitMQ-only routing, bounded RPC completion and value-42 probe behavior remain.
See [source ownership, credential policy and rollback](SECRETS.md).
