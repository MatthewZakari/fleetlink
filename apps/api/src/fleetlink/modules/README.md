# Bounded contexts

FL-009 implements `identity/domain`, `identity/application` and `identity/infrastructure`.
The domain has no framework dependencies. FL-010 adds immutable session lineages. Typed
application ports expose detached snapshots; SQLAlchemy adapters use caller-owned sessions
and transactions. There are no Identity
HTTP endpoints. See [Identity contracts](../../../../../docs/IDENTITY.md) and
[context ownership](../../../../../docs/ARCHITECTURE.md).

Commerce, Orders, Inventory, Logistics, Finance, Communication and Intelligence remain
planned. Do not create empty implementations or cross-context persistence access.
