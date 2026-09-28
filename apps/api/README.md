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
`infrastructure/database.py` owns the engine/session adapter and empty metadata registry;
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
