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
