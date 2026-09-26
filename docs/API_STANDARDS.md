# API standards

## Versioning and resources
Public REST APIs use /api/v1. Use plural resource nouns, stable UUID identifiers and standard HTTP semantics. GET is side-effect free; POST creates resources or explicit commands; PUT replaces a resource; PATCH applies a documented partial update; DELETE follows documented deletion semantics.
Use 200 for successful reads/updates, 201 with Location for creation, 202 for accepted asynchronous work with an operation/status reference, and 204 with no body where appropriate. A 202 never implies final business success.
Additive compatible evolution is preferred. Breaking changes need authorization, a version/deprecation plan, client migration guidance and contract tests.

## Request and response contracts
Use JSON with snake_case fields, explicit types, UUID strings, RFC 3339 timestamps with UTC Z, and documented null/omission semantics. Money uses exact decimal strings plus currency, with precision/rounding documented; internal minor units must map without loss.
Reject malformed input and unsupported writable fields, enforce payload limits and allowlist updates to prevent mass assignment. Single-resource responses contain the resource object; collections use { "items": [], "next_cursor": null, "has_more": false }.
Never expose persistence models, secrets, internal stack traces or private user data through public projections.

## Pagination, filters and sorting
Use opaque cursor pagination with limit (default 20, maximum 100 for v1 collection endpoints unless an endpoint documents a justified exception). These transport defaults are centrally configured, not duplicated business rules.
Use deterministic ordering with a UUID tie-breaker. Bind cursors to relevant filters/sort and validate them. Document consistency under concurrent writes; total counts are optional and never assumed.
Use allowlisted, typed query filters and comma-separated sort fields; a leading minus denotes descending order. Reject unsupported filters/sorts. Bound search complexity and geographic radius/range queries.

## Errors
Return application/problem+json with type, title, status, detail, instance, a stable code, correlation_id and optional field errors. Do not leak sensitive existence information.
Use 400 for malformed requests/cursors, 401 for missing/invalid credentials, 403 for denied access when disclosure is safe, 404 for missing or deliberately concealed resources, 409 for state/idempotency conflicts, 422 for semantic validation, 429 for throttling, and appropriate 5xx for server/provider failures. Map framework validation errors to this contract.
Clients use stable codes rather than parsing human text. Localize presentation on the client while preserving machine-readable codes.

## Authentication and authorization
Anonymous access is explicitly allowed for public marketplace reads. Protected endpoints validate bearer JWT access tokens following SECURITY.md. Authenticate the user, then enforce permissions, tenant/membership scope, ownership and resource state for every action.
A selected role or client-supplied user/merchant ID is never proof of authority. Document required permissions and anonymous access in OpenAPI.

## Idempotency and concurrency
Require Idempotency-Key for retryable critical commands such as order submission, payment attempts, refunds and ledger-affecting actions. Scope keys to authenticated principal, operation and applicable resource/tenant; fingerprint canonical validated payloads.
Atomically claim a key before effects. Same key/same payload replays the recorded outcome; a different payload returns 409. Concurrent or pending attempts return a documented conflict/status reference without repeating effects. Define retention from the domain/provider replay window before implementing each endpoint.
Do not cache transient pre-execution failure as a final business outcome. After uncertainty, reconcile using a stable provider reference. Event/webhook deduplication remains required independently.
Use documented resource versions or conditional updates to detect stale edits and return a consistent conflict response.

## Limits and tracing
Enforce centrally configured per-principal, per-IP and endpoint-sensitive limits as appropriate, with explicit proxy trust configuration. Return 429 and Retry-After when known. Apply separate limits to anonymous browsing, authentication, messaging, GPS and costly searches.
Accept a bounded validated X-Correlation-ID or generate a UUID; return it in responses and propagate it to logs/jobs/events. Support standard distributed trace propagation separately. Never trust correlation headers as identity.

## WebSockets and external callbacks
Document endpoint/version, authentication, origin checks where relevant, channel authorization, message schemas, event IDs/versions, heartbeat, rate limits, backpressure and reconnect/resync behavior. Revalidate permissions and expiry on long-lived connections; never place durable tokens in URLs.
Payment webhooks verify provider signatures and replay rules over raw bytes before processing. Persist receipt/deduplication durably, acknowledge according to provider requirements, and process idempotently. Treat callbacks as untrusted input.

## OpenAPI and contract gates
Publish OpenAPI for every public REST endpoint, including request/response schemas, examples without real data, errors, permissions, idempotency, pagination and limits. Maintain WebSocket/event documentation alongside it.
Review generated schema diffs, validate examples and run contract tests in CI once application tooling exists. Framework-generated documentation alone is insufficient if custom behavior is missing.

## FL-002 technical probes
The implemented /health and /ready routes are unversioned operational probes, not product APIs. /openapi.json documents them. Readiness reports application initialization only, explicitly declaring dependency_checks as not_configured. Its 503 uses the readiness report schema; other technical HTTP failures use the problem envelope. See [API bootstrap contract](../apps/api/README.md).
