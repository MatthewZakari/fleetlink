# FleetLink

FleetLink is a planned production-grade Commerce + Logistics Super App connecting customers, merchants, riders, and administrators through one user identity with multiple roles.

## Current status
FL-002: Monorepo Bootstrap, based on accepted FL-001 commit 6c4f4c9ef1d7c15eb1306563e4e6a727b1e568e7. The API implements only /health and /ready plus OpenAPI metadata. The Flutter foundation contains a running screen and Material 3 themes. There is no product functionality, database schema, external infrastructure or deployment. Flutter tooling was unavailable: native runner generation, dependency lock resolution, analyze and widget validation remain unverified (see apps/mobile/README.md).

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
- [Database principles](docs/DATABASE.md)
- [API standards](docs/API_STANDARDS.md)
- [Coding standards](docs/CODING_STANDARDS.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
- [Architectural decision records](docs/ADR/README.md)

## Repository conventions
```text
apps/api/             FastAPI src layout, technical core, tests and uv.lock
apps/mobile/          Flutter source, widget tests and minimal web runner
apps/admin/           Future admin purpose; framework undecided
services/worker/      Future background execution boundary
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
uv run --project apps/api --locked uvicorn fleetlink.main:create_app --factory --no-access-log
```

The server binds to loopback port 8000 by default. Check `http://127.0.0.1:8000/health`, `/ready` and `/openapi.json`. This local development listener is not a production ingress; production TLS remains required.
Environment settings use FLEETLINK_ENVIRONMENT and FLEETLINK_LOG_LEVEL. Defaults run locally without an environment file. To use optional root `.env` overrides, copy `.env.example` to `.env`, then add `--env-file .env` to `uv run`. No credentials are needed.

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
uv run --locked mypy src tests
```

`make help` lists equivalent root targets. The minimal GitHub Actions workflow runs locked backend install, lint/format, type checks and tests only; it does not deploy anything.
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

Flutter was unavailable during bootstrap. These checks are not claimed to pass; the generated pubspec.lock and Android/iOS runners require the SDK and must be reviewed/committed before native development. [Mobile setup](apps/mobile/README.md) contains exact generation commands, placeholder namespace, and remaining validation. No real corporate domain or release identifiers have been selected.
Riverpod and GoRouter have documented composition boundaries but no unused dependency installations. Admin framework selection remains a future ADR. No new significant architecture outside the approved direction is adopted.

See [testing standards](docs/TESTING.md) for future gates. Local bootstrap validation does not establish launch readiness.
