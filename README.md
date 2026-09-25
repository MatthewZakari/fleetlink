# FleetLink

FleetLink is a planned production-grade Commerce + Logistics Super App connecting customers, merchants, riders, and administrators through one user identity with multiple roles.

## Current status
FL-001: Repository Engineering Foundation. This repository currently contains authoritative engineering documentation and ignore rules only. There is no running application, database schema, authentication service, or deployment pipeline yet.

## Product and architecture
Anonymous visitors can browse the public marketplace. Authenticated users can access authorized customer, merchant, rider, and administrator experiences without separate accounts per role.
The backend starts as a modular monolith with explicit bounded contexts, Clean Architecture, domain-driven design, repository interfaces, and dependency injection. Reliable asynchronous workflows use transactional outbox delivery. Extract services only when measured needs justify the operational cost.

## Technology direction
- Mobile: Flutter, Dart, Material 3, Riverpod, GoRouter, Freezed, Dio, Hive, Flutter Secure Storage, Firebase Messaging, Google Maps, WebSockets, background services, and offline-first synchronization.
- Backend: Python, FastAPI, PostgreSQL with PostGIS, SQLAlchemy, Alembic, Redis, RabbitMQ, Celery, REST, WebSockets, OAuth2/OIDC, and JWT.
- Infrastructure: Docker, AWS, Cloudflare, object storage, GitHub Actions, and Kubernetes-ready deployment boundaries. Kubernetes is not an initial implementation requirement.
Versions, package choices, providers, and deployment topology must be validated and pinned during implementation; this list does not claim that dependencies are installed.

## Documentation
- [Agent instructions](AGENTS.md)
- [Product vision](docs/PRODUCT.md)
- [Architecture and context ownership](docs/ARCHITECTURE.md)
- [Implementation roadmap](docs/ROADMAP.md)
- [Database principles](docs/DATABASE.md)
- [API standards](docs/API_STANDARDS.md)
- [Coding standards](docs/CODING_STANDARDS.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
- [Architectural decision records](docs/ADR/README.md)

## Repository conventions
The current structure contains AGENTS.md, README.md, .gitignore, the eight subject documents above, and docs/ADR/README.md. Application directories will be introduced by later scoped tasks, not FL-001.
Use UUID identifiers, UTC internally, explicit types, bounded-context ownership, and consistent API contracts. Do not commit secrets or generated build output. Lockfiles for deployable applications must be committed when those applications exist.

## Planned development workflow
1. Select a scoped roadmap task with acceptance criteria.
2. Read AGENTS.md and relevant standards; inspect existing code.
3. Propose an ADR for significant architectural choices, including alternatives and tradeoffs.
4. Implement on a focused branch with behavior tests and documentation updates.
5. Run relevant checks, review security and compatibility impact, and open a pull request.
6. Merge after review and required checks; deploy through an auditable pipeline once established.

No setup, build, or test commands are advertised as working before tooling exists. See TESTING.md for planned validation gates. Local engineering decisions do not establish launch readiness or regulatory approval.
