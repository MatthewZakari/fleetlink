# API bootstrap

Python 3.12, uv 0.12.19. Run the root README commands. This is a src-layout installable application with a factory at fleetlink.main:create_app.

## Composition and settings
The factory validates Settings once, configures application logging, creates a per-app readiness instance and registers technical routes. FastAPI Depends resolves readiness from the application instance. Tests can inject Settings or override get_readiness; no service-container abstraction is needed.
Environment variables use the FLEETLINK_ prefix. environment accepts local/test/staging/production; log_level accepts DEBUG/INFO/WARNING/ERROR/CRITICAL. Invalid values fail startup. No external dependencies, secrets or dotenv auto-discovery are required. To load the root example explicitly, copy it to .env and use uv run --env-file .env --project apps/api --locked ... from the root.

## Technical HTTP contract
- GET /health â†’ 200 {"status":"ok"}; no dependency I/O.
- GET /ready â†’ 200 {"status":"ready","checks":{"application":"ready"},"dependency_checks":"not_configured"} after lifespan startup.
- Before initialization or after shutdown, /ready returns 503 with status/application set to not_ready. This probe's 503 intentionally carries a readiness report rather than a problem document.
- GET /openapi.json documents both probe schemas. Interactive documentation UIs are disabled. No business routes exist.
- X-Correlation-ID accepts one header containing 1â€“64 ASCII letters/digits/dot/underscore/hyphen, beginning with a letter or digit. Missing, invalid or duplicate values produce a new UUID. Every HTTP response includes the chosen ID, also present in error envelopes and application logs.
- 404, 405, 422 and unhandled pre-response 500 errors use application/problem+json with type, title, status, detail, instance (opaque URN), code and correlation_id. Exception details and invalid input values are omitted; 405 preserves Allow. Once a streaming response starts, failures cannot be replaced with a new envelope and must propagate.
- Logs are JSON with UTC timestamp, level, service, event, correlation_id and allowlisted timing/status/error-class fields. No request bodies, queries, settings or exception messages are logged. Disable Uvicorn access logs with the documented command to avoid raw URL logging.

Extend readiness in FL-003/FL-005 with bounded dependency probes, explicit degraded states and tests; do not infer database or broker health from the current response. Probe routing is an operational exception to /api/v1 business API versioning.

## Dependencies and reproducibility
pyproject.toml declares direct runtime and test/tool dependencies; uv.lock pins their resolved graph with hashes. Use uv sync --locked and uv run --locked so stale lockfiles fail. Python is constrained to 3.12 until expanded by validated CI.
Runtime: FastAPI (HTTP factory/DI/OpenAPI), Starlette (ASGI interfaces/middleware/errors), Pydantic (typed contracts), pydantic-settings (validated environment), Uvicorn (ASGI server).
Development: pytest (test runner), httpx2 (the installed Starlette TestClient's supported HTTP client), Ruff (lint/format), mypy (strict typing). Hatchling builds the src-layout package. Standard-library logging avoids another logging dependency. No pytest-asyncio is needed because TestClient drives ASGI lifespan in synchronous tests.
FastAPI/Starlette/Uvicorn/Pydantic/pytest/httpx2 use permissive licenses; review the exact resolved licenses and security advisories on upgrades. Ruff, mypy, uv and Hatchling are development/build tooling with permissive licenses. Versions are constraints plus a committed lock, not an assertion that packages never need maintenance.
To intentionally update dependencies: edit constraints if necessary, run uv lock --upgrade, review the lock diff and licenses/security impact, then run all checks. Never hand-edit the lockfile.
