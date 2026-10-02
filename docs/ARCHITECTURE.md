# Architecture

## Baseline direction
Start with a modular monolith: one backend codebase with independently owned domain modules, a PostgreSQL database, and separately scalable API and worker processes. This baseline is supplied by the FL-001 brief; significant later decisions and deviations require an ADR.
Use Clean Architecture, domain-driven design, repository interfaces, and dependency injection. Apply CQRS only when a specific read/write asymmetry justifies it. Microservice-ready means explicit contracts and data ownership, not immediate network hops.

## Context ownership
| Context | Authoritative responsibility |
| --- | --- |
| Identity | User identities, credentials/federation, sessions, role assignments, scoped memberships and access entitlements |
| Commerce | Merchant storefronts, products, catalog pricing, public discovery, reviews and review eligibility coordination |
| Orders | Carts, checkout coordination, order snapshots, order lifecycle and cancellation orchestration |
| Inventory | Stock, reservations, release/expiry and stock movement invariants |
| Logistics | Rider operational profiles, availability, dispatch, assignments, delivery lifecycle, GPS and route execution |
| Finance | Payment-provider integration, wallets, ledger, refunds, settlements and reconciliation |
| Communication | Messaging, notification preferences, templates and channel delivery |
| Intelligence | Analytics projections, model features, recommendations, forecasting and optimization proposals |
| Platform | Shared technical capabilities: configuration, telemetry, audit transport, health, storage adapters and operational tooling |
Administrators call authorized context use cases; Platform is not an alternate owner of domain rules. Identity owns rider/merchant access eligibility; Logistics and Commerce own their operational profiles. Finance alone owns monetary postings. Intelligence owns derived data, never replaces authoritative records.

## Layers and dependencies
- Domain: entities, value objects, invariants, domain services and events; no FastAPI, SQLAlchemy, broker, or Flutter coupling.
- Application: use cases, transactions, authorization orchestration and typed ports.
- Infrastructure: SQLAlchemy repositories, broker clients, provider integrations, cache and object storage adapters.
- Interface: REST/WebSocket endpoints, request validation and response mapping.
Dependencies point inward. The composition root wires concrete adapters to ports. Contexts expose application contracts and events. Do not import another context's persistence models or mutate its tables. Shared libraries contain genuinely shared technical primitives, not a second home for business rules.

## Interaction and consistency
Use in-process application calls for immediate coordination within the monolith. Each context controls its transactions. Cross-context workflows explicitly model pending, completed and compensated states; avoid assuming a transaction spans a remote payment provider or broker.
Checkout records a server-validated order snapshot, coordinates Inventory reservations and Finance payment attempts, and handles expiry, uncertain provider outcomes and compensation. Specific authorization/capture ordering and reservation policy need an ADR and provider requirements before implementation.
Write reliability-critical domain changes and outbox records in the same database transaction. A relay publishes committed records through RabbitMQ; Celery executes suitable background tasks. Delivery is at least once: stable event UUIDs, consumer deduplication, bounded retries, dead-letter handling and replay tooling are required. Do not promise exactly-once delivery.
Version event contracts; include event ID, type/version, UTC occurrence time, aggregate reference, correlation/causation IDs and minimal payload. Preserve ordering only where required, using aggregate sequence/version checks.

## Runtime components
FastAPI serves REST and authenticated WebSockets behind appropriate edge controls. PostgreSQL/PostGIS stores durable and geographic data. Redis provides ephemeral caching, distributed coordination and rate-limit support; it is not the financial source of truth. RabbitMQ buffers asynchronous delivery. Object storage holds permitted media and documents behind scoped access.
WebSocket processes must support horizontal fan-out without relying on a single process's memory. Recover missed updates through authorized REST snapshots or resumable event contracts; sockets are not durable business storage.
Deploy containerized API, workers and scheduled/relay processes separately. AWS and Cloudflare are the infrastructure direction; select managed services, networking and residency through later ADRs. Remain Kubernetes-ready without requiring Kubernetes initially.

## Mobile boundaries
Flutter/Material 3 presentation uses Riverpod for state and dependency composition, GoRouter for navigation, Freezed for immutable models, and Dio for REST transport. Keep domain/application behavior independent of widgets and transport DTOs.
Hive supports approved local cache and queued operations; Flutter Secure Storage holds suitable credentials. Firebase Messaging, Google Maps, WebSockets and background services are platform adapters.
Offline synchronization requires operation UUIDs, server revisions, retry policy, stale-data indicators and explicit conflict rules. Revalidate user entitlements and business invariants on sync. Never resolve stock, payment or wallet conflicts by blind last-write-wins. Clear or segregate per-user data on logout/account change.

## Operational qualities
Use structured logs, metrics and distributed traces with correlation propagation through HTTP, events and jobs. Track outbox age, queue lag, payment uncertainty, reconciliation exceptions and dispatch latency. Define service objectives and capacity targets before launch.
Use bounded timeouts, retries with jitter, circuit breaking where justified, graceful shutdown, readiness checks and restore-tested backups. Accessibility, localization, privacy and least privilege are cross-cutting acceptance requirements.
Extract a context only after an ADR demonstrates scaling, isolation or ownership benefits and addresses contract compatibility, data migration, observability and failure recovery.

## FL-002 implementation boundary
The initial src-layout API factory wires validated settings, JSON logging, request correlation, problem responses and application-lifecycle readiness. No domain module or external dependency integration exists. Flutter contains a minimal Material 3 shell and theme/localization boundaries; Riverpod and GoRouter remain planned. Native runner generation and Flutter SDK validation require available tooling; see [mobile status](../apps/mobile/README.md). Worker/realtime, shared packages, admin and infrastructure directories are documentation-only boundaries, not deployed services.

## FL-003 implementation boundary
Optional local Compose services provision PostgreSQL/PostGIS, Redis and RabbitMQ with
named development volumes and native validation. This supersedes the Docker placeholder
status above; all other future infrastructure boundaries remain unimplemented. The API
has no service clients or persistence and its probes remain infrastructure-independent.
See [local infrastructure](../infrastructure/docker/README.md).

## FL-004 implementation boundary
The existing factory remains the composition root. Technical HTTP registration is
centralized, native dependencies expose immutable settings/correlation/readiness, and
lifespan owns initialization and shutdown readiness. A shared JSON logging sink uses
request context for per-app log levels and correlation; factory construction has no
logging side effects. Pure ASGI middleware handles correlation, safe error responses,
completion timing and baseline response headers. No domain routers, external clients,
service container or new dependencies are introduced. This extends the accepted
architecture without a significant deviation requiring an ADR.

## FL-005 implementation boundary
SQLAlchemy 2.x/asyncpg adapters live in `apps/api/src/fleetlink/infrastructure/database.py`.
Each enabled application lifespan owns its engine and session factory; construction is
lazy with respect to connections and shutdown disposes the pool. Typed native dependencies
expose these resources without a service container. Sessions require explicit transactions.
The existing application-only readiness and health contracts are unchanged; neither is a
database availability assertion. FL-005 introduced no domain repositories or mappings; FL-009 adds the Identity mappings below.

Alembic executes outside application startup using an async connection and `run_sync`.
The technical baseline validates infrastructure-provisioned PostGIS and tracks revision
state without domain tables. Metadata is the authoritative explicit mapping registry (empty in FL-005);
autogeneration ignores reflected tables absent from that registry, protecting extension
and unrelated objects. Reviewed manual migrations are required for intentional table drops.
These choices implement the prescribed SQLAlchemy/Alembic architecture and FL-005 extension
policy; no significant architectural deviation or new context ownership requires an ADR.
See [database operations](DATABASE.md#fl-005-persistence-foundation).

## FL-006 implementation boundary

The existing API lifespan owns an optional lazy async Redis adapter and bounded pool.
Native DI exposes UUID-addressed TTL-bound operations; Redis holds no durable domain state.
The shared backend package composes a separate prefork Celery worker and synchronous producer.
RabbitMQ is the sole broker; only a strict technical probe is registered. No FastAPI worker
startup, domain event, outbox, scheduler or SQL schema is added.

JSON-only serialization, bounded retries, late acknowledgements, publisher confirms and
short-lived RabbitMQ RPC results provide diagnostics, not exactly-once execution or durable
business completion. The bounded RPC subclass is recorded in
[proposed ADR-0001](ADR/0001-technical-task-completion.md). API probes and FL-005 database
lifecycle remain compatible. [Operations](ASYNC_INFRASTRUCTURE.md) covers configuration,
resource limits, failure recovery, compatibility warnings and rollback.

## FL-007 implementation boundary

Platform telemetry uses explicitly owned OpenTelemetry API/SDK providers with opt-in
OTLP HTTP export, standard W3C HTTP/Celery transport propagation and independent log
correlation. API lifespans and worker prefork children own initialization and bounded
cleanup; no global providers or domain telemetry are introduced. Narrow native
instrumentation enforces safe, low-cardinality capture and preserves all FL-004 through
FL-006 contracts. The collector remains auxiliary and is not a readiness dependency.
See [observability](OBSERVABILITY.md) and [proposed ADR-0002](ADR/0002-opentelemetry-foundation.md).

## FL-008 implementation boundary

Platform retains frozen Settings and typed `SecretStr` credentials, with a narrow synchronous
`SecretSource` port and environment snapshot adapter. Resolution happens at settings construction
without filesystem/network discovery. Application consumers receive settings; clients capture
credentials at their existing infrastructure boundaries. Explicitly owned request/lifespan/worker
redaction contexts avoid process-global credential registries. Rotation requires drained client
recreation or process restart. No identity context, provider SDK or deployment work is added.
See [operations](SECRETS.md) and [proposed ADR-0003](ADR/0003-secrets-configuration-boundary.md).

## FL-009 Identity foundation

`modules/identity` is the first implemented bounded context. Pure immutable domain snapshots
own canonical user identity, status and platform role assignments. The application layer
exposes a typed `UserRepository` protocol and missing/conflict errors. Infrastructure maps
separate SQLAlchemy records onto the shared metadata; Alembic explicitly imports only these
intentional mappings. The existing unowned/PostGIS autogeneration filter remains intact.

The caller composes the adapter inside the existing explicit session/transaction contexts;
no service container, generic repository, Unit of Work, HTTP surface or import-time I/O is
introduced. Optimistic versions prevent stale whole-aggregate role/status replacement;
a single joined read returns a consistent snapshot. Roles describe eligibility, never
Commerce storefronts, Logistics profiles, resource ownership or administrator bypass.
See [Identity model and transaction contract](IDENTITY.md).

This implements the prescribed modular monolith using context-prefixed tables in the
existing namespace. No new physical schema, cross-context ownership, provider choice or
significant compatibility decision is adopted, so no FL-009 ADR is created. Authentication,
credentials, sessions, memberships, audit/outbox and production authorization remain deferred.

## FL-010 session persistence boundary

Identity now owns immutable authentication-session lineages and a typed session repository
alongside users. A unique family identifies one stable session; optimistic conditional
writes protect lifecycle changes and terminal revocation. User deletion is restricted while
sessions exist, pending explicit retention/deletion policy. No token protocol, token records,
authentication flow, authorization, provider, HTTP route or new runtime component is added.
The existing transaction/metadata/privacy boundaries apply. This scoped implementation of
the prescribed architecture needs no new ADR. Phase 1 remains incomplete. See
[session contracts and rotation boundary](IDENTITY.md#fl-010-authentication-session-foundation).
