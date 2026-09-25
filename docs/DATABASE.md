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
