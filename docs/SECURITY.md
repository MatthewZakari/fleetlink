# Security baseline

## Identity and sessions
Use an OAuth2/OIDC-compatible direction with explicit trust boundaries. Provider selection and first-party versus delegated credential ownership require an ADR. Mobile authorization should use authorization code with PKCE through supported system browser flows; never embed a client secret in the app.
Use short-lived JWT access tokens. Validate signature against an explicit algorithm allowlist, issuer, audience, expiry and relevant time claims; rotate signing keys and support bounded cache refresh. Reject arbitrary token-supplied key locations.
Use rotating refresh tokens with server-side hashed token records, family tracking, reuse detection, revocation and atomic rotation. Define concurrent refresh behavior without weakening replay detection. Logout and device removal revoke relevant refresh sessions; document access-token revocation latency.
Provide session/device listing, selective/global revocation and reauthentication for sensitive changes. Plan MFA enrollment, recovery and step-up capability; require stronger controls for privileged actions before production.

## Authorization
Authenticate one user with multiple assigned roles. Apply RBAC plus resource-level ownership, tenant membership, rider assignment and workflow-state checks. Deny by default and recheck each operation and subscription.
Role switching never grants access. Prevent cross-merchant data exposure, object-level authorization failures and mass assignment. Privileged grants and financial adjustments need auditable, restricted workflows.

## Credentials and abuse resistance
If FleetLink stores passwords, use a maintained adaptive password-hashing implementation, with Argon2id as the intended direction and parameters benchmarked on deployment hardware. Use unique salts through the library; keep any optional pepper in secret management. Never encrypt passwords for later recovery.
Use single-use, expiring verification/recovery challenges stored safely. Avoid account enumeration, credential stuffing and reset abuse through consistent responses, centrally configured rate limits and monitoring. Apply quotas to uploads, searches, messaging, GPS and WebSockets.

## Secrets and encryption
Never commit secrets, live credentials or personal data. Use managed secret storage and workload identities where possible; separate environments, limit permissions, rotate and audit access. Secret scans must cover changes and incident review of history where needed.
Require encrypted network transport and managed encryption at rest for databases, backups and object storage. Document key ownership and rotation. Restrict origins, upload types/size, object access and signed-URL lifetimes. Do not treat Cloudflare as a replacement for application authorization.
Use least privilege for database roles, AWS identities, CI credentials, brokers and containers; avoid root containers and broadly shared credentials.

## Payment, event and location trust
Verify payment webhook signatures over the original bytes using provider rules, validate freshness where supported and deduplicate provider event IDs. Do not trust return URLs, client payment-success flags or unsigned callbacks. Reconcile uncertain outcomes with the provider.
Minimize payment data; select provider-hosted/tokenized flows and confirm applicable obligations before launch. Do not store card security codes.
Authorize GPS producers and viewers, limit retention and precision to approved needs, obtain permissions/consent and make tracking state visible. Validate timestamps/ranges and handle spoofed or stale input.
Treat broker payloads and AI inputs/outputs as untrusted. Models cannot bypass authorization, post ledger entries or independently expand tool privileges.

## Audit, privacy and response
Record protected audit events for sign-in risk, session/role changes, administrative actions, financial operations and sensitive access. Include actor, action, target, outcome, UTC time and correlation reference without secret payloads.
Restrict audit writes/read access, detect tampering and define retention/deletion policies with legal/operational review. Minimize data in logs, analytics and development fixtures.
Maintain threat models and incident response plans, credential-revocation procedures and tested restoration workflows. Launch geography, privacy obligations and financial controls remain decisions to resolve before dependent production use.

## Verification gates
Establish dependency and secret scanning, SAST, container/image scanning and infrastructure review in CI. Run DAST against authorized nonproduction environments with safe test data. Prioritize findings by exploitability/impact, assign owners and prohibit unexplained suppression.
Test resource-level authorization, session replay, token expiry/key rotation, injection, SSRF, file handling and rate-limit bypass. Review externally exposed or financial flows before launch; record accepted risks and remediation deadlines.
These are requirements for future tooling and delivery, not claims of completed scans or certification.

## Implemented FL-004 technical baseline
The current API provides sanitized technical errors, bounded correlation headers,
context-isolated structured logs and explicit nosniff/framing/referrer/cache response
headers. Debug and interactive docs are disabled; no cross-origin permissions are
installed. The documented local server command disables access logs, server identity
headers and proxy-header trust. See [API operations](../apps/api/README.md) for limits,
including propagated streaming failures and deferred TLS/ingress policy. Authentication,
authorization, rate limiting and production hardening remain separately scoped work.

## FL-006 broker/Redis boundary

Redis and Celery are opt-in through immutable Settings. Credentials are hidden from repr,
AMQP URL components are separately encoded, and connectivity errors omit driver text.
The documented worker entry point disables the banner and uses structured events instead
of raw library messages, full payloads or tracebacks. Do not log client objects, URLs or
raw Pydantic diagnostics. Synthetic UUIDs supply task correlation.

JSON-only serializers and strict worker validation reduce deserialization risk; they do
not authorize publishers. The sole task performs no domain mutation. Bounded key TTLs,
queue backlog, reply retention, pools and retries limit resource use. RPC results are
disposable, never financial/task ledgers. Future business consumers need explicit broker
permissions, idempotency and dead-letter/replay ownership. Private local services retain
development credentials and unencrypted transport; production TLS, ACL provisioning and
topology remain future work. See [operations](ASYNC_INFRASTRUCTURE.md).

## FL-007 telemetry exfiltration boundary

Opt-in telemetry export is an information-exfiltration boundary, independent of request
and task authorization. Allowlist source-defined route templates, bounded method/status
classes, fixed task names/outcomes and dependency systems only. Never capture authorization
headers, cookies, tokens/JWTs, bodies, customer payloads, SQL text/parameters, Redis
keys/values, broker credentials or full connection URLs. Trace IDs support correlation,
not identity or access control. Do not copy baggage or arbitrary span attributes into logs.
Tracestate/baggage are dropped; correlation IDs remain a separate existing log contract.

Exporter endpoint credentials/query/path are rejected; endpoint repr and formatted
configuration failures hide inputs. Do not log raw validation error dictionaries.
Native OTEL overrides are rejected when enabled to prevent implicit capture, credentials,
resource metadata or exporter changes. Exporter/SDK failure diagnostics are sanitized and
rate-limited within the telemetry boundary; unrelated warnings remain visible. No automatic
exception message/stack capture is enabled. Collector access, TLS/egress, residency,
retention and upstream sampling trust require production review before enabling export.
No production credentials, collector or SaaS integration is introduced. See
[the complete privacy/cardinality policy and limitations](OBSERVABILITY.md).

## FL-008 implemented configuration boundary

Current database/broker credentials, optional Redis ACL credentials and the sensitive OTLP
origin are explicitly classified. Representation and structured validation diagnostics mask
secret inputs. Scoped redaction and structural event/header allowlists protect controlled
logs and Problems; driver error messages remain omitted. CI scans Git history and working
files using the checksum-pinned MIT Gitleaks CLI 8.30.1, with no directory exclusions.
[Secrets operations](SECRETS.md) defines local defaults, production validation, rotation,
incident response, rollback and precise limitations. These protections do not secure process
memory, external debug handlers or arbitrary direct SDK calls.

## FL-009 Identity boundary

Canonical UUID users, bounded account statuses and multiple platform roles now persist in
Identity-owned tables. UUIDs and role labels are not credentials or authorization evidence.
`active` is account state, not verified identity. An administrator role grants no bypass;
future access policy must combine assigned roles with ownership, membership, resource state
and context-specific rules. Merchant/rider role assignment creates no operational profile.

There are no Identity HTTP endpoints, credentials, passwords, provider identifiers, token protocols,
verification/recovery, authentication middleware or authorization implementation.
FL-010 adds internal session persistence as described below.
No personal profile data is stored. Persistence errors use the existing sanitized session
boundary; domain snapshots are not logged or added to telemetry labels. FL-007 SQL privacy
and FL-008 redaction/scanning remain unchanged. These internal mutation primitives require
future authorized use cases and audit policy before exposure. This is not production
Identity readiness or Phase 1 completion. See [Identity contracts](IDENTITY.md).

## FL-010 session persistence boundary

Identity sessions now persist UUID ownership, lifetime, stable unique family, lifecycle
and optimistic version. They contain no tokens, verifiers, credentials, copied roles or
profiles. Active session state is not authentication or authorization. Conditional saves
cannot undo revocation or overwrite a newer version; user deletion is restricted while
session evidence exists. Expiry is evaluated from explicit time, without a state mutation.

Raw bearer/refresh tokens must never be persisted or logged. No token format or hashing
algorithm is selected here. FL-011 adds bounded one-way verifier records, atomic persistence
rotation and consumed-token history below; protocol and reuse response remain deferred. Do not log session
snapshots, cookies, Authorization headers, SQL/parameters or token/verifier data; do not
use user/session/family/token identifiers as metric labels or secret data in telemetry.
Existing FL-007/FL-008 boundaries remain unchanged. HTTP authentication, JWT, providers,
credentials, MFA/recovery, authorization and production readiness remain deferred. See
[Identity contracts](IDENTITY.md#fl-010-authentication-session-foundation).

## FL-011 refresh evidence boundary

Only non-secret UUID lookup IDs and bounded one-way verifier evidence are persisted beneath
the stable session. Never provide raw bearer tokens or reversible ciphertext. The verifier
wrapper and containing snapshot omit verifier bytes from repr; no exceptions or telemetry
capture values. Do not serialize or log snapshots, direct verifier values or SQL parameters.
No global secret registry, FL-007 capture change or FL-008 scanner exception is added.

Rotation conditionally advances both session and old-token versions in a caller-owned
transaction, preserving consumed history and one current lineage head. Terminal consumption
and session revocation cannot be undone through the adapter. Consumed presentation has an
explicit internal reuse exception; possession must be verified before a future service
acts on it. Confirmed reuse should revoke the relevant family under reviewed policy, not
silently succeed or trigger an invented global-account policy. No protocol, credential
verification, auth endpoint or production readiness is claimed. See
[contracts and limits](IDENTITY.md#fl-011-refresh-token-rotation-foundation).

## FL-012 refresh-credential protocol boundary

FL-012 supersedes the historical protocol deferrals above, pending independent security
review. [ADR-0004](ADR/0004-refresh-token-protocol.md) specifies a public candidate UUID
separate from 32 OS-generated random secret bytes. The strict versioned parser rejects
malformed, oversized, noncanonical and unsupported inputs. UUID possession alone proves
nothing. Only version-tagged, identifier-bound SHA-256 evidence is persisted through the
existing FL-011 envelope; no raw or reversibly encoded bearer material enters records.
Equal-length secret-derived digests use `hmac.compare_digest`; public grammar and lookup
metadata are not claimed timing-secret. The construction is for high-entropy random
secrets, never passwords. No signing key, pepper or provider architecture is selected.

Credentials and generation results hide raw material from repr/str. Fixed protocol errors
never echo inputs; no logs, metrics or spans are emitted. Explicit `reveal()` returns a
sensitive wire string for a future reviewed transport. Never persist or log it, capture
it in diagnostics, serialize credential objects or inspect their locals in shared tooling.
Python immutable objects do not guarantee memory erasure; restrict debugger/core-dump and
third-party capture access. Configuration redaction does not register generated tokens.

Successful verification proves possession only, even for consumed/expired evidence.
Future callers must separately enforce lifecycle and prove possession before responding
to reuse, then use FL-011 caller-owned atomic rotation. Stolen bearer material remains
replayable; read-only verifier disclosure does not supply a usable credential. Full
database-write/runtime compromise is outside this construction's protection. Authentication
flows, abuse controls, authorization and production readiness remain deferred; Phase 1 is
incomplete. See [Identity protocol contracts](IDENTITY.md#fl-012-refresh-token-protocol-foundation).

## FL-013 refresh acceptance and replay boundary

FL-013 adds Identity application orchestration using existing protocol and repository ports.
Possession verification precedes consumed-token replay interpretation. CURRENT evidence,
active/unexpired owning sessions and ACTIVE accounts are required for normal rotation;
SUSPENDED/DISABLED accounts cannot refresh. Roles grant no authorization here.
Replacement expiry equals the current record's absolute expiry, never a sliding duration
or extension to session expiry. Confirmed reuse revokes only its stable session/family,
never the account, roles or unrelated sessions. Existing session/token CAS is reused with
no automatic conflict retry and no cryptography in repositories.

The caller owns the transaction. `ProvisionalRefresh` exposes sensitive wire text only via
explicit `reveal()` after commit; `ProvisionalRefreshReuse` returns denial without a
credential so revocation can commit. Discard either result on rollback/commit failure.
All exceptions must escape the transaction and existing sanitized database boundary.
No logs, telemetry, identifier metric labels, raw credential persistence, SQL parameter
capture or global generated-secret registry is added. Repr/str hide sensitive material;
never serialize these objects or capture their internals in diagnostics.

No dependency, TTL setting, schema or migration is added; Alembic stays at
`0004_refresh_token_rotation`. ADR-0004 remains Proposed pending independent security review.
Phase 1 remains incomplete. No login/registration transport, HTTP refresh/logout, access
JWT/JWKS, authentication middleware, provider/password architecture, OAuth/OIDC/PKCE,
passwords, MFA/recovery, authorization or mobile auth UI is implemented. FL-013 does not
establish production Identity readiness. See [the complete application/transaction contract](IDENTITY.md#fl-013-refresh-authentication-service).

## FL-014 unmounted HTTP authentication boundary

No public Identity route is added. The adapter collapses allowlisted missing/invalid,
expired/reused, account/session-unavailable and optimistic-conflict failures into one fixed
401 Problem without internal details. Validation inputs are omitted; unexpected failures,
including commit and cleanup failures, use Platform's sanitized 500. Exception mapping is
route-local and does not change existing probe or unrelated HTTP behavior.

The operation boundary commits once before returning provisional results to a handler;
operation/commit failures roll back and never release a successful result. Confirmed reuse
must complete normally through commit before becoming a denial. Tests exercise these rules
through test-mounted routes, including PostgreSQL deferred constraints, and check credential,
header, body and exception-message omission from controlled diagnostics. No new capture or
redaction registry is added. Uniform response content does not guarantee uniform timing.
Post-commit response failure and uncertain commit acknowledgement require future endpoint
policy; do not retry acceptance automatically. See the
[complete boundary contract](IDENTITY.md#fl-014-http-boundary-foundation).

Provider architecture, public auth challenges/transport, access tokens, password flows,
authorization, rate limits and production hardening remain deferred. ADR-0004 remains Proposed;
FL-014 awaits independent review and does not complete Phase 1.
