# Bounded contexts

FL-009 implements `identity/domain`, `identity/application` and `identity/infrastructure`.
The domain has no framework dependencies. FL-010 adds immutable session lineages; FL-011
adds bounded refresh-token evidence and atomic rotation persistence. FL-012 adds
standard-library versioned generation, parsing and possession verification in
`identity/application/refresh_protocol.py`, without persistence or transaction ownership.
Only derived one-way evidence may enter domain/repository snapshots; raw credentials
must never enter persistence, logs or telemetry. Typed
application ports expose detached snapshots; SQLAlchemy adapters use caller-owned sessions
and transactions. There are no Identity
HTTP endpoints. See [Identity contracts](../../../../../docs/IDENTITY.md) and
[context ownership](../../../../../docs/ARCHITECTURE.md).

The [refresh protocol ADR](../../../../../docs/ADR/0004-refresh-token-protocol.md) remains
proposed for independent security review. Possession is not refresh acceptance or
authorization. FL-013 adds internal refresh acceptance below; HTTP authentication flows
remain deferred and Phase 1 remains incomplete.

Commerce, Orders, Inventory, Logistics, Finance, Communication and Intelligence remain
planned. Do not create empty implementations or cross-context persistence access.

## FL-013 application boundary

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
establish production Identity readiness. See [the complete application/transaction contract](../../../../../docs/IDENTITY.md#fl-013-refresh-authentication-service).

## FL-014 interface boundary

`identity/interface/http` supplies an unmounted route adapter, fixed typed authentication
problems and native request composition. An explicit operation runner commits before returning
results and rolls back failures; repositories and domain objects retain their existing
contracts. Only tests mount demonstration routes. No public Identity workflow is implemented.
See [usage and limitations](../../../../../docs/IDENTITY.md#fl-014-http-boundary-foundation).
