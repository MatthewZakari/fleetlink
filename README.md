# FleetLink

FleetLink is a planned production-grade Commerce + Logistics Super App connecting customers, merchants, riders, and administrators through one user identity with multiple roles.

## Current status

FL-009 begins Phase 1 with an Identity domain and PostgreSQL persistence foundation,
pending independent review. Canonical UUID users have bounded account status and multiple
platform roles. FL-010 adds immutable authentication-session lineages and PostgreSQL
persistence, pending independent review. FL-011 adds refresh-token records and atomic rotation
persistence, also pending independent review. FL-012 adds versioned refresh-credential
generation and possession verification using one-way evidence, pending independent review.
No authentication flow, authorization or Identity HTTP endpoints are implemented.
Phase 1 is not complete. See [Identity contracts](docs/IDENTITY.md) and
[proposed refresh protocol ADR](docs/ADR/0004-refresh-token-protocol.md).

FL-008 adds classified secret settings, an environment snapshot source, scoped diagnostic
redaction and checksum-pinned CI secret scanning, pending independent review. See
[secrets operations](docs/SECRETS.md). No identity/provider functionality is added.

FL-007 implements an opt-in OpenTelemetry foundation for HTTP/infrastructure tracing,
Celery transport propagation, technical metrics and trace/log correlation, pending
independent review. Export is disabled by default; no collector is required for startup
or tests. See [observability contracts and operations](docs/OBSERVABILITY.md).

FL-006 implements optional Redis and RabbitMQ/Celery technical infrastructure, pending independent review. It adds a dedicated worker and a harmless transport probe, with no business jobs. See [async infrastructure operations](docs/ASYNC_INFRASTRUCTURE.md).

FL-005 adds an optional async PostgreSQL persistence foundation and Alembic technical baseline to the accepted FL-001 through FL-004 foundation. The API implements only /health and /ready plus OpenAPI metadata. The Flutter foundation contains a running screen and Material 3 themes. FL-003 adds optional local PostgreSQL/PostGIS, Redis and RabbitMQ infrastructure. FL-005 added no product functionality, domain schema or production deployment; FL-009 now adds only the Identity tables described below. Flutter analysis and the three widget/theme tests passed during FL-003 validation; native runner work remains outside this task (see apps/mobile/README.md).

## Product and architecture
Anonymous visitors can browse the public marketplace. Authenticated users can access authorized customer, merchant, rider, and administrator experiences without separate accounts per role.
The backend starts as a modular monolith with explicit bounded contexts, Clean Architecture, domain-driven design, repository interfaces, and dependency injection. Reliable asynchronous workflows use transactional outbox delivery. Extract services only when measured needs justify the operational cost.

## Technology direction
- Mobile: Flutter, Dart, Material 3, Riverpod, GoRouter, Freezed, Dio, Hive, Flutter Secure Storage, Firebase Messaging, Google Maps, WebSockets, background services, and offline-first synchronization.
- Backend: Python, FastAPI, PostgreSQL with PostGIS, SQLAlchemy, Alembic, Redis, RabbitMQ, Celery, REST, WebSockets, OAuth2/OIDC, and JWT.
- Infrastructure: Docker, AWS, Cloudflare, object storage, GitHub Actions, and Kubernetes-ready deployment boundaries. Kubernetes is not an initial implementation requirement.
This is the long-term technology direction. Only bootstrap dependencies listed in apps/api/pyproject.toml and apps/mobile/pubspec.yaml are introduced now. Backend transitive versions are pinned in apps/api/uv.lock; providers and deployment topology remain future decisions.

## Documentation
- [Agent instructions](AGENTS.md)
- [Product vision](docs/PRODUCT.md)
- [Architecture and context ownership](docs/ARCHITECTURE.md)
- [Implementation roadmap](docs/ROADMAP.md)
- [Identity foundation](docs/IDENTITY.md)
- [Database principles](docs/DATABASE.md)
- [API standards](docs/API_STANDARDS.md)
- [Coding standards](docs/CODING_STANDARDS.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
- [Observability](docs/OBSERVABILITY.md)
- [Architectural decision records](docs/ADR/README.md)

## Repository conventions
```text
apps/api/             FastAPI src layout, technical core, tests and uv.lock
apps/mobile/          Flutter source, widget tests and minimal web runner
apps/admin/           Future admin purpose; framework undecided
services/worker/      Worker boundary; runtime in the shared API package
services/realtime/    Conditional future realtime separation
packages/contracts/  Future versioned contracts
packages/shared/     Genuinely shared technical primitives only
infrastructure/      Docker, Kubernetes, Terraform, monitoring placeholders
tests/               Future E2E, performance and security suites
docs/                Engineering specification and ADR process
```
Directory READMEs explain ownership; a placeholder does not mean an implementation exists.
Use UUID identifiers, UTC internally, explicit types, bounded-context ownership, and consistent API contracts. Do not commit secrets or generated build output. Lockfiles for deployable applications must be committed when those applications exist.

## Planned development workflow
1. Select a scoped roadmap task with acceptance criteria.
2. Read AGENTS.md and relevant standards; inspect existing code.
3. Propose an ADR for significant architectural choices, including alternatives and tradeoffs.
4. Implement on a focused branch with behavior tests and documentation updates.
5. Run relevant checks, review security and compatibility impact, and open a pull request.
6. Merge after review and required checks; deploy through an auditable pipeline once established.

## Prerequisites and backend setup
Install Python 3.12 and uv 0.12.19 (for example, `python -m pip install uv==0.12.19`). GNU Make is optional. No Docker, database or broker is required.
From the repository root:

```sh
uv sync --project apps/api --locked
uv run --project apps/api --locked uvicorn fleetlink.main:create_app --factory --no-access-log --no-server-header --no-proxy-headers
```

The server binds to loopback port 8000 by default. Check `http://127.0.0.1:8000/health`, `/ready` and `/openapi.json`. This local development listener is not a production ingress; production TLS remains required.
Environment settings use FLEETLINK_ENVIRONMENT and FLEETLINK_LOG_LEVEL. Defaults run locally without an environment file. To use optional root `.env` overrides, copy `.env.example` to `.env`, then add `--env-file .env` to `uv run`. Credentials are needed only when explicitly using persistence; the API reuses the Compose PostgreSQL variables.

## Local infrastructure

FL-003 provisions optional services. FL-005 adds opt-in API persistence resources while preserving application-only readiness.
Run `make infra-up`, `make infra-check`, and `make infra-down` from the repository root.
See [local infrastructure setup](infrastructure/docker/README.md) for environment
variables, ports, Codespaces networking, persistence and destructive reset instructions.

## Backend validation
From the repository root:

```sh
uv run --project apps/api --locked pytest apps/api/tests
uv run --project apps/api --locked ruff check apps/api
uv run --project apps/api --locked ruff format --check apps/api
```

Type checking uses the API's configuration (change directory first):

```sh
cd apps/api
uv run --locked mypy src tests tests_db tests_broker migrations
```

`make help` lists equivalent root targets. GitHub Actions runs locked backend install, lint/format, type checks, tests and checksum-pinned secret scans; it does not deploy anything.
See [API configuration and dependency rationale](apps/api/README.md), including the warning-free httpx2 test client and why pytest-asyncio is unnecessary.

## Mobile setup and validation
Install a current stable [Flutter SDK](https://docs.flutter.dev/install/archive). From the repository root:

```sh
cd apps/mobile
flutter pub get
flutter analyze
flutter test
flutter run -d chrome
```

Flutter was unavailable during the original bootstrap; analysis and tests have since passed during FL-003 validation. Android/iOS runners still require separate review before native development. [Mobile setup](apps/mobile/README.md) contains exact generation commands, placeholder namespace, and remaining validation. No real corporate domain or release identifiers have been selected.
Riverpod and GoRouter have documented composition boundaries but no unused dependency installations. Admin framework selection remains a future ADR. No new significant architecture outside the approved direction is adopted.

See [testing standards](docs/TESTING.md) for future gates. Local bootstrap validation does not establish launch readiness.

## Persistence foundation (FL-005)

See [database operations](docs/DATABASE.md#fl-005-persistence-foundation) for explicit
configuration, privileges, transaction ownership and migration rollback. Resources are
created during lifespan only when `FLEETLINK_DATABASE_ENABLED=true`; construction does
not connect. `/ready` does not verify database health. Migrations run separately:

```sh
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-upgrade
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-current
make api-db-history
```

These commands use exported configuration; they never discover `.env` implicitly.
For custom credentials, explicitly load the intended root `.env` using the documented
`uv run --env-file .env` pattern. `make api-test` stays infrastructure-free.
See [isolated database tests](docs/TESTING.md#fl-005-database-validation) for setup,
`make api-test-db`, and explicitly named cleanup commands.

## Redis and task foundation (FL-006)

`make api-test` remains infrastructure-free, including Redis/Celery unit tests.
`make api-test-tasks` selects those tests alone. With reachable private services:

```sh
make infra-up
FLEETLINK_CELERY_ENABLED=true make api-worker
# In another terminal with matching broker settings:
FLEETLINK_CELERY_ENABLED=true make api-task-smoke
```

`make api-test-broker` starts isolated real workers and verifies completion, retries,
failure, restart, timeout and cleanup. Missing services cause failure. See
[test setup and Codespaces connectivity](docs/TESTING.md#fl-006-broker-validation),
[configuration and dependencies](docs/ASYNC_INFRASTRUCTURE.md), and
[the proposed result-backend ADR](docs/ADR/0001-technical-task-completion.md).
Publishing is not completion; Redis is not a second broker or durable ledger.
