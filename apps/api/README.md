# API technical foundation

Python 3.12, uv 0.12.19. Run the root README commands. This is a src-layout installable application with a factory at fleetlink.main:create_app.

## Composition and settings
The factory validates Settings once, creates per-app settings/readiness state and centrally registers technical middleware and exception handlers. It does not configure logging at import or factory time. Lifespan installs an idempotent process-wide JSON sink, marks the instance ready, and resets readiness in a finally block on shutdown. Request log levels and correlation use context variables, so constructing or serving another app does not change an existing app's request logging policy. Outside requests the sink defaults to INFO. Native FastAPI dependencies `get_settings` and `get_correlation_id` live in `core/dependencies.py`; `get_readiness` remains in `core/readiness.py`. Tests can inject immutable Settings or use dependency overrides; no service container is needed.
Environment variables use the FLEETLINK_ prefix. environment accepts local/test/staging/production; log_level accepts DEBUG/INFO/WARNING/ERROR/CRITICAL. Invalid values fail construction; human-readable validation diagnostics omit rejected values. Do not log Settings or raw Pydantic error dictionaries. With persistence disabled, no external services or secrets are required. There is no dotenv auto-discovery. To load the root example explicitly, copy it to .env and use uv run --env-file .env --project apps/api --locked ... from the root.

## Technical HTTP contract
- GET /health â†’ 200 {"status":"ok"}; no dependency I/O.
- GET /ready â†’ 200 {"status":"ready","checks":{"application":"ready"},"dependency_checks":"not_configured"} after lifespan startup.
- Before initialization or after shutdown, /ready returns 503 with status/application set to not_ready. This probe's 503 intentionally carries a readiness report rather than a problem document.
- GET /openapi.json documents both probe schemas and problem responses for 404/405/422/500, explicitly using application/problem+json. Interactive documentation UIs are disabled. No business routes exist.
- X-Correlation-ID accepts one header containing 1â€“64 ASCII letters/digits/dot/underscore/hyphen, beginning with a letter or digit. Missing, invalid or duplicate values produce a new UUID. Every HTTP response includes the chosen ID, also present in error envelopes and application logs.
- 404, 405, 422 and unhandled pre-response 500 errors use application/problem+json with type, title, status, detail, instance (opaque URN), code and correlation_id. Exception details and invalid input values are omitted; 405 preserves Allow. Once a streaming response starts, failures cannot be replaced with a new envelope and must propagate.
- Logs are JSON with UTC timestamp, level, service, event, correlation_id and allowlisted timing/status/error-class fields. No request bodies, queries, settings or exception messages are logged. Disable Uvicorn access logs with the documented command to avoid raw URL logging.

External dependency probes remain deferred to a separately authorized task; do not infer database or broker health from the current response. Probe routing is an operational exception to /api/v1 business API versioning.

## Dependencies and reproducibility
pyproject.toml declares direct runtime and test/tool dependencies; uv.lock pins their resolved graph with hashes. Use uv sync --locked and uv run --locked so stale lockfiles fail. Python is constrained to 3.12 until expanded by validated CI.
Runtime: FastAPI (HTTP factory/DI/OpenAPI), Starlette (ASGI interfaces/middleware/errors), Pydantic (typed contracts), pydantic-settings (validated environment), Uvicorn (ASGI server).
Development: pytest (test runner), httpx2 (the installed Starlette TestClient's supported HTTP client), Ruff (lint/format), mypy (strict typing). Hatchling builds the src-layout package. Standard-library logging avoids another logging dependency. No pytest-asyncio is needed because TestClient drives ASGI lifespan in synchronous tests.
FastAPI/Starlette/Uvicorn/Pydantic/pytest/httpx2 use permissive licenses; review the exact resolved licenses and security advisories on upgrades. Ruff, mypy, uv and Hatchling are development/build tooling with permissive licenses. Versions are constraints plus a committed lock, not an assertion that packages never need maintenance.
To intentionally update dependencies: edit constraints if necessary, run uv lock --upgrade, review the lock diff and licenses/security impact, then run all checks. Never hand-edit the lockfile.

## FL-004 security and operations
Every HTTP response, including errors and uninitialized readiness, receives
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, and `Cache-Control: no-store`. These prevent MIME
sniffing, framing, referrer forwarding and caching of technical responses.
No CORS middleware or cross-origin permission is enabled. Debug mode and interactive
documentation remain disabled in every environment; the same conservative headers
apply locally, in tests, staging and production. Environment selection does not grant trust.

Use `make api-run`: Uvicorn access logging, server identity headers and forwarded-proxy
header interpretation are explicitly disabled. Direct requests provide the connection
metadata; the application does not infer trust from Forwarded/X-Forwarded-* headers.
A future ingress must explicitly define trusted proxies and TLS policy. HSTS is deferred
until that TLS topology is defined. These controls are a baseline, not production security.

Validation errors intentionally remain generic: even field locations can contain
attacker-controlled mapping keys. No rejected values, locations or validator messages
are reflected. Existing error codes and the seven-field Problem envelope are unchanged.
Response-started failures propagate without a second response; the application logs only
the exception class. ASGI server/operator logging of propagated failures needs separate
production review. Application log messages must be fixed event names, never secrets.

No dependencies, persistence or deployment resources were added. Rollout requires only
restarting the API; rollback restores the prior code and launch command, with no data
migration. Existing probe bodies, status codes and correlation rules remain compatible;
new response headers and OpenAPI failure descriptions are additive.

## FL-005 persistence
The FL-004 statements above describe that milestone; FL-005 adds SQLAlchemy/asyncpg and
Alembic plus a direct declaration of the existing AnyIO dependency. See
[configuration, lifecycle, transactions and dependency rationale](../../docs/DATABASE.md#fl-005-persistence-foundation).
`infrastructure/database.py` owns the engine/session adapter and explicit metadata registry;
`core/dependencies.py` exposes typed `get_database` and `get_session`. Factory construction
performs no database I/O or engine creation. Lifespan resource enablement is explicit via
`FLEETLINK_DATABASE_ENABLED`; default false preserves isolated factory tests.

`alembic.ini`, `migrations/env.py`, and `migrations/versions/0001_technical_baseline.py`
provide migration tooling separately from startup. Install with `uv sync --locked`
(including the default dev group) for migration execution. The baseline validates PostGIS
and manages only revision tracking. Future metadata imports must be deliberate; migrations
never discover arbitrary domain modules or create tables at application startup.

`make api-test` collects only `tests/`; [database validation](../../docs/TESTING.md#fl-005-database-validation)
uses `tests_db/` and explicit test database configuration. Strict mypy includes `src`,
`tests`, `tests_db` and `migrations`. No pytest-asyncio is added; async tests use `asyncio.run`.
No database availability is implied by `/ready`, and `/health` performs no database work.

## FL-006 Redis and worker infrastructure

`infrastructure/redis.py` provides per-lifespan opt-in async Redis via `get_redis`.
Factory construction performs no connection attempts. `infrastructure/broker.py` composes
RabbitMQ-only Celery and a bounded synchronous producer; `infrastructure/tasks.py` registers
only `fleetlink.technical.probe.v1`. `python -m fleetlink.worker` runs a dedicated prefork
worker; `python -m fleetlink.technical_smoke` publishes and independently reads completion.
Neither runs inside FastAPI. Both require `FLEETLINK_CELERY_ENABLED=true`.

See [configuration, dependencies, delivery semantics and rollback](../../docs/ASYNC_INFRASTRUCTURE.md).
Direct runtime additions are redis, Celery and its existing Kombu runtime; celery-types is
development-only. Strict mypy covers `src tests tests_db tests_broker migrations`; Ruff
covers all `apps/api`. `make api-test-tasks` is infrastructure-free; `make api-test-broker`
uses real services/processes. No endpoints, database mappings, domain jobs or readiness
dependency checks are added.

## FL-007 observability

Telemetry export is disabled by default and never a readiness dependency. The existing
factory/lifespan, pure ASGI HTTP context, immutable Settings and JSON sink remain in place.
Per-instance OpenTelemetry providers add privacy-safe traces and duration metrics; valid
active spans add trace/span IDs to logs independently of correlation IDs. Worker providers
start after fork. PostgreSQL events and Redis adapter operations never capture SQL,
parameters, keys, values or credentials. Celery uses standard W3C transport headers without
payload changes. See [configuration, ownership, privacy, dependency rationale and rollback](../../docs/OBSERVABILITY.md).
`make api-test-observability` selects infrastructure-free telemetry contracts; existing CI
already covers the new modules/tests. No collector or external SaaS is installed.

## FL-008 configuration security foundation

Frozen settings and existing `SecretStr` fields now use an injectable environment snapshot
source. Credential repr/JSON, rejected validation inputs and controlled diagnostics are
sanitized. Use `Settings.diagnostic_configuration()` and `connection_target()` rather than
raw settings/client internals or connection URLs. Enabled staging/production database and
broker clients reject development defaults. Local/test defaults remain compatible.

`make api-test-secrets` selects the dedicated infrastructure-free regressions. CI also scans
full Git history and the working tree with checksum-pinned Gitleaks 8.30.1; run
`make secret-scan` locally. No Python dependency or lockfile change is introduced.
See [source injection, classification, rotation and limits](../../docs/SECRETS.md).

## FL-009 Identity foundation

`modules/identity` provides pure users/status/roles, typed repository ports and separate
SQLAlchemy records/adapters. Alembic explicitly imports those mappings and revision
`0002_identity_foundation` creates two context-owned tables. Caller-owned transactions and
optimistic snapshots prevent hidden commits and stale replacement. No Identity route,
authentication, authorization or startup migration is added. See [Identity contracts](../../docs/IDENTITY.md),
[destructive rollback](../../docs/DATABASE.md#fl-009-identity-schema-and-migration) and
[tests](../../docs/TESTING.md#fl-009-identity-validation).

## FL-010 session foundation

Identity adds immutable session lineages, typed repository ports and caller-owned optimistic
SQLAlchemy persistence. Migration `0003_auth_session_foundation` creates only
`identity_auth_sessions`; downgrade to FL-009 destroys session data while retaining users.
No token/verifier, auth endpoint, middleware, provider or dependency is added. Phase 1
remains incomplete. See [session contracts](../../docs/IDENTITY.md#fl-010-authentication-session-foundation),
[migration operations](../../docs/DATABASE.md#fl-010-session-schema-and-migration) and
[validation](../../docs/TESTING.md#fl-010-session-validation).

## FL-011 refresh-token persistence foundation

Identity adds immutable refresh evidence, a typed `add`/`get`/`rotate` port and atomic
session/token optimistic writes. `0004_refresh_token_rotation` adds only
`identity_refresh_tokens`. UUID lookup identifiers are not bearer credentials; bounded
one-way verifier bytes are excluded from representations. No raw tokens are stored.
Repositories never own transactions. No protocol, HTTP authentication flow, dependency or
telemetry change is introduced. Phase 1 remains incomplete. See
[refresh contracts](../../docs/IDENTITY.md#fl-011-refresh-token-rotation-foundation),
[migration safety](../../docs/DATABASE.md#fl-011-refresh-token-schema-and-migration) and
[validation](../../docs/TESTING.md#fl-011-refresh-token-validation).

## FL-012 refresh protocol foundation

`modules/identity/application/refresh_protocol.py` adds standard-library secure generation,
strict versioned parsing, identifier-bound SHA-256 evidence and safe possession comparison.
Generated credentials expose wire text only via explicit sensitive `reveal()`; never log,
serialize into diagnostics or persist that text. Caller-created records contain only UUID
lookup metadata and the 45-byte tagged verifier. Generation and verification own no
persistence or transactions. Domain/repository contracts and Alembic head are unchanged.

No dependency, lockfile, settings, HTTP endpoint, middleware or authentication flow is added.
Phase 1 remains incomplete; implementation awaits independent security review. See
[protocol contracts](../../docs/IDENTITY.md#fl-012-refresh-token-protocol-foundation),
[proposed ADR-0004](../../docs/ADR/0004-refresh-token-protocol.md) and
[validation](../../docs/TESTING.md#fl-012-refresh-protocol-validation).
