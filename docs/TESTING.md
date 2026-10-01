# Testing strategy

## Evidence and scope
Testing is mandatory for new behavior. FL-001 was documentation-only. FL-002 adds infrastructure-free API tests in apps/api/tests and Flutter widget tests in apps/mobile/test; see the root README for commands and mobile tooling limitations. Do not describe planned checks as completed.
Bootstrap tooling establishes reproducible backend commands; future tasks must extend fixtures and validation for their behavior. Record command, environment, result and important limitations for executed checks. Failed or unavailable checks remain visible.

## Test layers
| Layer | Required coverage |
| --- | --- |
| Unit | Domain invariants, state transitions, policy boundaries, value objects, validation and deterministic failure paths |
| Integration | Repository/adapters, transactions, outbox relay, broker consumers, Redis behavior and provider boundaries |
| API | OpenAPI contracts, error shape, pagination, anonymous reads, authentication, permissions, idempotency and concurrent retries |
| Database | PostgreSQL/PostGIS constraints, spatial behavior, isolation, competing stock/wallet writes, deadlocks and query plans |
| Migration | Fresh install and upgrades from supported baselines, representative data/backfills, rolling compatibility and recovery |
| Flutter | Domain/state tests, widget tests, navigation, role switching, localization, accessibility and offline cache isolation |
| E2E | Customer-to-merchant-to-rider journey, multi-role identity, administrative denial paths, cancellation and recovery |
| WebSocket | Handshake/channel authorization, expiry/revocation, reconnect/resync, missed/duplicate/out-of-order events and backpressure |
| Payment | Provider sandbox, signed webhooks, replay/tampering, duplicate charge prevention, refunds, uncertainty and reconciliation |
| GPS simulation | Valid/invalid coordinates, stale/out-of-order updates, poor accuracy, route changes, offline replay and unauthorized viewing |
| Performance | Load, spike and soak workloads; p95/p99 latency, throughput, resource saturation, queue lag and database budgets |
| Security | Authorization matrix, cross-tenant access, session reuse, scanning, abuse limits and controlled DAST |

## Critical invariants
Test that one user can switch assigned roles without creating another identity, and cannot activate unassigned privileges. Public browsing must not expose private fields.
Prove that concurrent inventory reservations do not oversell, repeated critical commands do not repeat effects, posted ledger transactions balance per currency, and corrections preserve immutable history.
Inject failures before/after database commit, before/after event publish, and during provider timeouts. Assert outbox recovery, deduplicated effects and bounded retry/dead-letter behavior. Test safe reconciliation when success is uncertain.
Offline tests must cover revoked permissions, account changes, expired sessions, stale prices and duplicate synchronization. Pending actions must never appear falsely confirmed.

## Environments and data
Use isolated, reproducible dependencies and synthetic fixtures. Database integration tests must use PostgreSQL/PostGIS rather than assuming SQLite-equivalent behavior.
Mock external services for fast deterministic cases, then run sandbox contract/integration checks. Never charge real customers or copy unsanitized production data into tests.
Use controlled clocks, seeded randomness and explicit timezones. Exercise supported mobile OS versions and background permission restrictions; simulators do not replace critical device testing.

## Planned CI and release gates
Pull requests should run formatting, lint/type checks, unit and targeted integration/API tests, contract checks and required security scans. Broader E2E, migration, performance and device suites run at appropriate merge/release or scheduled gates.
Set coverage targets from risk; coverage percentage alone is not a release criterion. Identity, money, stock and delivery-state invariants require explicit negative and concurrency tests.
Before production, agree on service objectives and load models, verify migrations/backups/restore, rehearse rollback and resolve release-blocking security findings. Quarantined flaky tests require an owner and deadline; they cannot silently substitute for required evidence.

## FL-004 technical core coverage
`make api-test`, `make api-lint` and `make api-typecheck` validate the infrastructure-free
core. Tests cover independent factories, immutable/environment settings, dependency
overrides, pre/post-lifespan readiness, error/OpenAPI contracts, security headers,
structured UTC logs and sensitive-data omission. Direct ASGI tests use an asyncio
barrier to force concurrent requests without sleep-based timing, and verify cancellation
and failures after response start never transmit a second response. Test-only routes
are registered on isolated instances and never appear in the shipped API.
Run `make infra-config`, `python3 infrastructure/docker/test_validate.py`,
`make mobile-analyze` and `make mobile-test` for adjacent milestone regressions.

## FL-005 database validation
The default `make api-test` still selects only `apps/api/tests`, including configuration,
secret handling, no-network lifecycle and explicit transaction tests. It does not start
Docker, create databases or migrate. `make api-test-db` explicitly selects `tests_db` and
fails if configuration/infrastructure is absent; it never silently skips database checks.
No SQLite substitution exists. The fixture requires exactly
`FLEETLINK_POSTGRES_DB=fleetlink_test_fl005` before any connection or migration.

From the root, with the intended Compose configuration:

```sh
make infra-up
make api-db-test-setup
# On the daemon host the default host is 127.0.0.1. Otherwise set an authorized reachable host.
FLEETLINK_POSTGRES_DB=fleetlink_test_fl005 make api-test-db
# Explicit test-only destruction, after recording evidence:
make api-db-test-drop
```

If root `.env` contains custom credentials, load it explicitly for the Python process:

```sh
uv run --env-file .env --project apps/api --locked env \
  FLEETLINK_POSTGRES_DB=fleetlink_test_fl005 pytest apps/api/tests_db
```

The final override is deliberate: never run this suite against `fleetlink_dev`. Override
host/port explicitly if required; do not print credential values. The fixed dedicated test
database must be newly created for each complete suite; the migration test rejects existing
revision tracking, rather than silently claiming a fresh migration. Setup refuses existing
databases. To repeat the full suite, explicitly run the test-only drop and setup commands.
Never use `infra-reset` to prepare tests, and do not run suites concurrently.

Real PostgreSQL tests cover authenticated access and invalid-password failure, PostGIS
extension/version and SRID 4326 operations, session cleanup, committed temporary-table
writes, injected-failure rollback, cancelled-task rollback, refused connections and pool
disposal. Migration tests upgrade a fresh database, repeat head, downgrade to base, and
re-upgrade, asserting revision state and PostGIS survival. Only temporary/test-owned objects
and Alembic revision tracking are used; no domain entities or fixtures exist.

Required regressions remain `make api-test`, `make api-lint`, `make api-typecheck`,
`make infra-config`, `python3 infrastructure/docker/test_validate.py`,
`make mobile-analyze` and `make mobile-test`. Record failures/unavailable checks separately;
mocked lifecycle tests do not establish PostgreSQL integration success.

## FL-006 broker validation

`make api-test` still selects infrastructure-free `tests/`, including Redis/Celery tests.
`make api-test-tasks` selects `tests/test_broker.py` alone. They cover immutable settings,
safe credentials/URLs, disabled resources, independent apps/pools, partial-startup cleanup,
cancellation, sanitized failures, strict payloads, retry bounds and safe logs. Neither
mocked calls nor pure task execution are claimed as integration evidence. Strict mypy
includes `src tests tests_db tests_broker migrations`; Ruff includes all API Python files.

Run the real suite explicitly against the existing private development services:

```sh
make infra-up
make api-test-broker
```

The target sets `FLEETLINK_BROKER_TESTS=1` and a 180-second outer deadline. Direct pytest
requires that opt-in. Missing services fail clearly, never skip. To load custom `.env`:

```sh
FLEETLINK_BROKER_TESTS=1 timeout --kill-after=10s 180s \
  uv run --env-file .env --project apps/api --locked pytest apps/api/tests_broker
```

Python must reach both services. In this Linux Codespace, reachable private container
addresses can be discovered for one invocation (never commit those addresses):

```sh
FLEETLINK_REDIS_HOST=$(docker inspect fleetlink-local-redis-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}') \
FLEETLINK_RABBITMQ_HOST=$(docker inspect fleetlink-local-rabbitmq-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}') \
  make api-test-broker
```

This is not universally reachable with Docker-outside-of-Docker. Otherwise run from the
daemon host or an authorized environment on `fleetlink-local-network` with `redis` and
`rabbitmq` DNS names and internal ports 6379/5672. Keep loopback publications private.
Use the actual configured credentials, without logging values.

Each run generates new UUID task queues/direct exchanges and namespaced Redis/reply keys.
It checks Redis PING, set/get, observed TTL expiry, exact deletion, connection cleanup,
refused connections and rejected ACL identity. It publishes without a worker and requires
a completion timeout, starts a real prefork subprocess, then verifies the deterministic
result, two bounded retries, exhaustion on attempt 3, rejection on attempt 1, graceful
TERM, and queued work completing after worker restart. Separate checks reject invalid
RabbitMQ credentials and exercise a stalled TCP/AMQP handshake. A test-only TCP proxy
forwards an actual publish but withholds its confirmation: the producer must time out
while a passive real-broker check observes the accepted message. This proves uncertainty,
not successful completion.

Completion uses polled broker state under deadlines, not a sleep as proof. Redis expiry
polling likewise observes the key disappear. Tests preserve an unrelated sentinel key/queue
while deleting only their exact resources, then remove their own sentinels. They do not
purge shared queues, flush Redis, change ACLs, reset volumes or touch PostgreSQL. Prefork
workers get 20 seconds to shut down; a forced kill fails the test. Repeated runs cannot
reuse old task IDs/results. A hard-killed suite can leave a durable UUID task queue/exchange;
inspect and delete only that exact recorded name, never the standard development queue.
Reply queues expire after 60 seconds and Redis keys after their bounded TTL.

CI remains infrastructure-free; it does not claim broker coverage. Record explicit
`make api-test-broker` evidence separately. Run the unchanged FL-005 setup/test/drop
workflow to verify PostgreSQL/Alembic when available. Required adjacent checks remain
`make infra-config`, `python3 infrastructure/docker/test_validate.py`, `make mobile-analyze`,
`make mobile-test`, backend tests/lint/types and `git diff --check`. See
[operational limits and rollback](ASYNC_INFRASTRUCTURE.md).

## FL-007 observability validation

`make api-test` still requires no services or collector. `make api-test-observability`
selects in-memory trace/metric and mocked OTLP contracts: default disablement, endpoint
and numeric validation, resource identity, W3C continuation/malformed headers, payload-free
Celery propagation, retries, log correlation/concurrency, safe metrics/route templates,
privacy, sampling, cancellation, repeated lifespans, child lifecycle, exporter failures
and bounded shutdown. A SQLite event fixture checks listeners only and does not replace
PostgreSQL integration. Ruff/format/strict mypy/pytest already cover every new directory.

The unchanged isolated FL-005 setup/test/drop commands above also exercise async PostgreSQL
query instrumentation without SQL/parameter capture. The existing `make api-test-broker`
also checks real RabbitMQ-to-prefork-worker W3C trace continuity across a retry, observing
worker log trace IDs against the producer's in-memory span. That test enables export only
to a deliberately refused loopback port, proving completion and cleanup survive collector
failure without contacting an external backend. Queue/exchange/reply cleanup stays exact
and isolated. Preserve development volumes and clean up the test database and workers.
These checks establish neither successful collector/backend interoperability nor production
monitoring readiness. See [observability operations and limitations](OBSERVABILITY.md).

## FL-008 secrets validation

Run `make api-test-secrets` without services. Generated fake sentinels exercise typed repr,
settings/validation JSON, injected environment snapshots and absent credentials, safe targets,
driver/broker failures, worker shutdown, structured logs, seven-field Problems and headers,
OTel span/event/resource/metric capture, rotation snapshots and concurrent context isolation.
The suite asserts that known in-memory sentinels never appear in captured diagnostic surfaces.
It is also included in `make api-test`; keep the observability and real-service suites above.

Run `make secret-scan` with Gitleaks 8.30.1 (or set `FLEETLINK_GITLEAKS_BIN` to its binary).
Both history and working-tree scans redact findings. Fixtures need no scanner allowlist.
See [classification, scanner installation and limits](SECRETS.md). Database integration still
requires fresh `fleetlink_test_fl005`, explicit setup/drop and cleanup verification.
