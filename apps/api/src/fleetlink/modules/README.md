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
authorization; authentication flows remain deferred and Phase 1 remains incomplete.

Commerce, Orders, Inventory, Logistics, Finance, Communication and Intelligence remain
planned. Do not create empty implementations or cross-context persistence access.
