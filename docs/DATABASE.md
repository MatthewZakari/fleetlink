# Database design principles

## Scope and ownership
PostgreSQL with PostGIS is the durable data direction. SQLAlchemy provides infrastructure persistence and Alembic manages schema migrations. FL-001 creates no tables, models or migrations.
Each bounded context owns its records, constraints and write interfaces. Logical schema separation may reinforce ownership; adopt a physical layout in an ADR. Do not access another context's tables from domain code. Any cross-context foreign keys or reporting joins require explicit extraction and migration tradeoff review.

## Types and integrity
Use UUID primary/resource identifiers and timezone-aware UTC timestamps. Select UUID generation strategy before implementation. Geographic fields must specify coordinate reference system, units, valid ranges and spatial indexing needs.
Represent money with exact decimal or integer minor units plus currency; choose one canonical representation per contract, document currency exponents and explicit rounding. Never use binary floating point for money. Quantities need explicit units and precision.
Enforce invariants with not-null, uniqueness, checks and appropriate foreign keys as well as domain validation. Choose indexes from actual access paths; assess query plans and tenant-scoped uniqueness. UUIDs are identifiers, not authorization credentials.

## Transactions and concurrency
Define transaction boundaries per use case. Use optimistic versions or targeted row locking for competing updates; retry only safely repeatable work. Inventory reservation and wallet posting must resist concurrent overspend/oversell.
Use durable, scoped idempotency records with payload fingerprints and outcome state. Atomic uniqueness/locking prevents simultaneous duplicate processing. Persist critical domain changes and outbox entries together. Consumers deduplicate event processing atomically with local effects.
Avoid holding database transactions open during network calls. Model uncertain external outcomes and reconcile them.

## Financial integrity
Finance owns an immutable double-entry ledger. Each posting transaction balances debits and credits per currency, associates entries with a business reference, and commits atomically. Cross-currency movements require explicit balanced currency legs and exchange policy.
Correct errors through linked reversals and new entries, never rewriting posted history. Derived wallet balances must be reconcilable to ledger entries; cached balances are not independent authority. Separate pending holds, posted funds and available funds through documented rules. Enforce unique external references and reconcile provider statements.

## Evolution and operations
Migrations must be reviewed, repeatable from supported baselines, tested against PostgreSQL/PostGIS, and designed for rolling deployment. Prefer expand/backfill/contract; assess lock duration and large-data backfill checkpoints.
State whether rollback is safe; destructive changes need backup/restore or forward-fix plans and explicit authorization. Do not assume an automatic downgrade can recover deleted data.
Use least-privilege database roles, encrypted connections/storage, connection budgets, slow-query monitoring and restore-tested backups. Define RPO/RTO, retention, archival and partitioning from approved needs and observed scale, not guesses.
Classify personal, location, financial and audit data. Document retention/deletion and legal-hold rules before launch. Restrict sensitive exports and production data in development; anonymize fixtures. No universal soft-delete rule should override privacy or financial integrity.

## FL-005 persistence foundation

### Configuration and local setup
`Settings` remains immutable and reads environment variables only. No dotenv file is
implicitly loaded. `URL.create` constructs the `postgresql+asyncpg` URL without string
interpolation, including reserved characters in credentials. User/password fields are
`SecretStr` and excluded from Settings repr. Never log Settings, raw validation error
dictionaries, driver exceptions or connection URLs (URL repr can still expose a username).
The adapter's connection/session boundaries sanitize driver and transport failures into
`DatabaseError`; application exceptions and cancellation propagate. Low-level engine access
is for infrastructure code only and does not provide that error boundary. SQL echo is off
and bound parameters are hidden. Production TLS/role provisioning remains deferred.

All names below use the `FLEETLINK_` prefix:

| Suffix | Default | Meaning / bounds |
| --- | --- | --- |
| `DATABASE_ENABLED` | `false` | Enable API lifespan resources; does not enable probes or migrations |
| `POSTGRES_HOST` | `127.0.0.1` | Host reachable from the API process |
| `POSTGRES_PORT` | `5432` | Same published Compose port; 1–65535 |
| `POSTGRES_DB` | `fleetlink_dev` | Same database as Compose; must be explicitly exported for migrations/tests |
| `POSTGRES_USER` | `fleetlink_dev` | Same local Compose role |
| `POSTGRES_PASSWORD` | `development-only-postgres` | Same local placeholder; override outside disposable local use |
| `DATABASE_POOL_SIZE` | `5` | 1–50 persistent connections per app/process |
| `DATABASE_MAX_OVERFLOW` | `5` | 0–50 additional connections per app/process |
| `DATABASE_POOL_TIMEOUT` | `5` | Pool checkout seconds, greater than 0 and at most 60 |
| `DATABASE_CONNECT_TIMEOUT` | `5` | Driver connect seconds, greater than 0 and at most 60 |
| `DATABASE_COMMAND_TIMEOUT` | `30` | Driver command seconds, greater than 0 and at most 300 |

NaN/infinity and out-of-range numeric values fail at Settings/application construction.
The total connection budget is `(pool_size + max_overflow) × application processes`.
There is one source of PostgreSQL credentials shared with Compose, not a second DSN.
Run `make infra-up` then `make infra-check` using the existing infrastructure workflow.
Compose explicitly reads root `.env` or `.env.example`; the API requires explicit loading:

```sh
uv run --env-file .env --project apps/api --locked \
  uvicorn fleetlink.main:create_app --factory --no-access-log --no-server-header --no-proxy-headers
```

Set `FLEETLINK_DATABASE_ENABLED=true` in that chosen environment to enable resources.
For Docker-outside-of-Docker, see the infrastructure README: host publication may not be
reachable at devcontainer localhost. Use an authorized reachable database host; never
broaden the listener to public interfaces. Containers on the existing network use
`postgres:5432`; the published port variable applies to host clients only.

### Connections, sessions and transactions
The factory creates no engine; each enabled lifespan creates a `Database` and stores it
on app state. Engine construction opens no connection. The first explicit operation
checks out a connection; pool pre-ping validates reused connections. Shutdown clears state
and disposes idle connections after request sessions finish. Disabled or out-of-lifespan
resource dependencies fail explicitly. Readiness continues to report only application
initialization with `dependency_checks=not_configured`, never database health.

`Database.connection()` is a short-lived connection context. `Database.session()` or
`get_session` acquires a fresh, non-concurrent session without beginning or committing a
transaction. `autobegin=False`, `autoflush=False`, and `expire_on_commit=False` make work
explicit. A use case owns the transaction, using SQLAlchemy's existing context manager:

```python
async with database.session() as session:
    async with session.begin():
        # Explicit SQL or future application-owned persistence work goes here.
        pass
```

Entering `begin()` establishes the transaction boundary; the first SQL needs a connection.
Normal exit from `begin()` commits; exception/cancellation exit rolls back. Alternatively,
a caller can `await session.begin()`, then explicitly `commit()` or `rollback()`.
Closing a session rolls back remaining work and releases its connection; it never commits.
Cleanup is shielded from AnyIO cancellation. Do not share sessions across concurrent tasks,
hold transactions through remote calls, or automatically commit HTTP requests. No speculative
Unit of Work or generic repository is introduced; the native transaction context suffices.

### Migrations, extension policy and rollback
Infrastructure/database administrators provision PostGIS. The accepted Compose validator
is authoritative for the development database; the isolated test bootstrap uses the same
privileged provisioning policy. Application migrations never create/drop PostGIS.
In production, administration owns databases/extensions/roles, a migration role owns
application DDL/revision tracking, and runtime roles receive only necessary data privileges.
The shared superuser role in local Compose is a **local-only simplification**, not a
production privilege design.

`apps/api/alembic.ini` contains no URL. `migrations/env.py` uses an async engine with
`NullPool` and the supported Alembic `run_sync` pattern. Revision
`0001_technical_baseline` fails with a fixed prerequisite message when PostGIS is missing.
It adds no domain tables or schemas. Alembic owns `alembic_version`.

```sh
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-upgrade
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-current
make api-db-history
# DESTRUCTIVE with FL-009: removes Identity data; preserves PostGIS/unrelated objects.
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-downgrade-base
FLEETLINK_POSTGRES_DB=fleetlink_dev make api-db-upgrade
```

Supply the actual intended database/host and shared credentials explicitly. When using
root `.env`, use `uv run --env-file .env --project apps/api --locked alembic -c
apps/api/alembic.ini upgrade head` on one shell line. No migration runs at FastAPI startup.
Run only one migration executor per database. A failed baseline transaction leaves no
partial revision state; provision prerequisites and retry. Downgrading the baseline alone removes its revision entry, leaving Alembic's version
table, PostGIS and unrelated data intact. With FL-009 installed, downgrade to base first
drops Identity tables and their data. See the destructive rollback warning below.

Offline SQL is supported with `alembic -c apps/api/alembic.ini upgrade head --sql` using
`uv run --project apps/api --locked`. It needs no credentials/connection, emits a PostgreSQL
DO block to validate PostGIS when executed, and cannot verify server availability while
generating SQL. Review and apply it explicitly to the selected database. Future mappings
must register on the infrastructure metadata and be imported before autogeneration.
Extension/unowned tables are excluded from autogeneration; review generated revisions.

### Dependency rationale and limitations
| Dependency | Purpose and alternatives | Compatibility, license and maintenance |
| --- | --- | --- |
| SQLAlchemy 2.x with asyncio extra | Typed engine/pool/session and future mappings; raw asyncpg would duplicate lifecycle/ORM integration; synchronous SQL blocks async handlers | Python 3.12; MIT; maintain 2.0-compatible APIs and review pinned upgrades; asyncio extra supplies greenlet (MIT AND PSF-2.0) |
| asyncpg | Native asynchronous PostgreSQL driver for the selected dialect; psycopg async is viable but adds an alternate driver without need | Python 3.12; Apache-2.0; review PostgreSQL compatibility and compiled wheels on upgrades |
| Alembic (dev group) | Versioned migrations outside runtime startup; handwritten SQL alone lacks standard revision tracking | Python 3.12; MIT; migration runners install the dev tooling group; Mako/MarkupSafe are transitive tooling dependencies |
| AnyIO (already installed transitively, now direct) | Shield cleanup under FastAPI/Starlette cancellation; asyncio shielding alone does not cover AnyIO level cancellation | Python 3.12; MIT; no additional installed package; keep aligned with Starlette's supported range |

uv resolves and pins all dependencies; no lockfile is edited manually. GeoAlchemy2 is
unnecessary without geographic ORM columns. These libraries require regular advisory and
license review; passing tests is not a vulnerability scan. Operational costs are bounded
connection pools and a separately invoked migration tool, with no new service. SSL policy,
production credentials/roles, dependency readiness and geographic mappings remain separately
scoped work; FL-009 adds only the Identity repository/schema described below. No FL-006 functionality is included.

Implementation follows [SQLAlchemy async documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
and [Alembic async integration](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).

## FL-009 Identity schema and migration

Revision `0002_identity_foundation` follows `0001_technical_baseline`. It creates only
`identity_users` and `identity_user_roles` in the existing namespace. See the exact
[columns, constraints, UUID and concurrency contract](IDENTITY.md). No extension, physical
schema, credential table or cross-context foreign key is introduced. The two primary keys
supply the only indexes: UUID lookup and per-user role retrieval/uniqueness. No speculative
status/role-wide search index exists. Alembic `check` verifies supported schema comparisons during integration tests, including
an unrelated sentinel table. Explicit PostgreSQL tests verify checks, uniqueness and foreign
keys; Alembic alone does not compare every constraint.

Upgrade remains `FLEETLINK_POSTGRES_DB=<intended-db> make api-db-upgrade`; no migration runs
at startup. Old technical-only API code does not access the new tables. Prefer application
rollback leaving these additive tables intact when data must survive.

**Destructive downgrade:** the following explicitly removes both Identity tables and all
their data, preserving PostGIS, unrelated objects and the technical baseline. Back up data
and choose a forward fix or reviewed restore strategy before rollback outside isolated tests.
Re-upgrade recreates empty tables; it does not recover deleted identities.

```sh
FLEETLINK_POSTGRES_DB=<intended-db> uv run --project apps/api --locked \
  alembic -c apps/api/alembic.ini downgrade 0001_technical_baseline
FLEETLINK_POSTGRES_DB=<intended-db> make api-db-upgrade
```

Use an actual explicitly intended database name in place of `<intended-db>`. Never run the
integration suite against development; use the [guarded FL-005 workflow](TESTING.md#fl-005-database-validation).
