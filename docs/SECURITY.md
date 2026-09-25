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
