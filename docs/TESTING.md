# Testing strategy

## Evidence and scope
Testing is mandatory for new behavior. FL-001 has no application code or runnable test suite; validate document structure, relative links, scope and consistency only. Do not describe planned checks as completed.
Future tooling tasks must establish reproducible commands and fixtures. Record command, environment, result and important limitations for executed checks. Failed or unavailable checks remain visible.

## Test layers
| Layer | Required coverage |
| --- | --- |
| Unit | Domain invariants, state transitions, policy boundaries, value objects, validation and deterministic failure paths |
| Integration | Repository/adapters, transactions, outbox relay, broker consumers, Redis behavior and provider boundaries |
| API | OpenAPI contracts, error shape, pagination, anonymous reads, authentication, permissions, idempotency and concurrent retries |
| Database | PostgreSQL/PostGIS constraints, spatial behavior, isolation, competing stock/wallet writes, deadlocks and query plans |
| Migration | Fresh install and upgrades from supported baselines, representative data/backfills, rolling compatibility and recovery |
| Flutter | Domain/state tests, widget tests, navigation, role switching, localization, accessibility and offline cache isolation |
| E2E | Customer-to-merchant-to-rider journey, multi-role identity, administrative denial paths, cancellation and recovery |
| WebSocket | Handshake/channel authorization, expiry/revocation, reconnect/resync, missed/duplicate/out-of-order events and backpressure |
| Payment | Provider sandbox, signed webhooks, replay/tampering, duplicate charge prevention, refunds, uncertainty and reconciliation |
| GPS simulation | Valid/invalid coordinates, stale/out-of-order updates, poor accuracy, route changes, offline replay and unauthorized viewing |
| Performance | Load, spike and soak workloads; p95/p99 latency, throughput, resource saturation, queue lag and database budgets |
| Security | Authorization matrix, cross-tenant access, session reuse, scanning, abuse limits and controlled DAST |

## Critical invariants
Test that one user can switch assigned roles without creating another identity, and cannot activate unassigned privileges. Public browsing must not expose private fields.
Prove that concurrent inventory reservations do not oversell, repeated critical commands do not repeat effects, posted ledger transactions balance per currency, and corrections preserve immutable history.
Inject failures before/after database commit, before/after event publish, and during provider timeouts. Assert outbox recovery, deduplicated effects and bounded retry/dead-letter behavior. Test safe reconciliation when success is uncertain.
Offline tests must cover revoked permissions, account changes, expired sessions, stale prices and duplicate synchronization. Pending actions must never appear falsely confirmed.

## Environments and data
Use isolated, reproducible dependencies and synthetic fixtures. Database integration tests must use PostgreSQL/PostGIS rather than assuming SQLite-equivalent behavior.
Mock external services for fast deterministic cases, then run sandbox contract/integration checks. Never charge real customers or copy unsanitized production data into tests.
Use controlled clocks, seeded randomness and explicit timezones. Exercise supported mobile OS versions and background permission restrictions; simulators do not replace critical device testing.

## Planned CI and release gates
Pull requests should run formatting, lint/type checks, unit and targeted integration/API tests, contract checks and required security scans. Broader E2E, migration, performance and device suites run at appropriate merge/release or scheduled gates.
Set coverage targets from risk; coverage percentage alone is not a release criterion. Identity, money, stock and delivery-state invariants require explicit negative and concurrency tests.
Before production, agree on service objectives and load models, verify migrations/backups/restore, rehearse rollback and resolve release-blocking security findings. Quarantined flaky tests require an owner and deadline; they cannot silently substitute for required evidence.
