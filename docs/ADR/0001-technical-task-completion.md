# ADR-0001 — Bounded technical task completion over RabbitMQ RPC

- Status: Proposed; implemented for FL-006 independent review, not milestone acceptance.
- Date: 2026-09-29.
- Owners/reviewers: FleetLink maintainers, implementer and independent reviewer.

## Context and decision

FL-006 authorizes technical Redis and RabbitMQ/Celery infrastructure. Publish confirmation
cannot prove execution. Tests and the smoke command need an independently read deterministic
result without domain tables or a durable task ledger.

Use Celery RabbitMQ RPC with a narrow subclass: namespaced reply queues, 60-second expiry,
32-message / 64 KiB caps and no automatic publication retries. Results are transient;
queue metadata is durable because RabbitMQ 4.3 rejects non-durable non-exclusive queues.
Do not enable that deprecated feature. Poll terminal RPC metadata with a bounded deadline
to avoid Celery 5.6.3 async-consumer reconnect on ordinary socket timeout. Producers own
two-connection pools and delete only their own reply queues.

This is an ephemeral diagnostic, not audit storage or business completion guarantee.
Late acknowledgements, bounded task retries and acknowledged terminal failures permit
duplicates or lost observations. Reliable future workflows still require the accepted
outbox, deduplication and domain-specific recovery design.

## Alternatives

- Redis results add a cross-service dependency and another retention path to a broker probe.
- PostgreSQL results add unnecessary durable infrastructure/schema scope.
- Logs alone cannot give the producer a result without collecting another process's output.
- Custom completion messages reimplement Celery's existing result protocol.
- Unmodified RPC defaults conflict with RabbitMQ 4.3 and leave retry/resource limits implicit.
- Exclusive queues require shared publishing/consuming connections, unlike Celery RPC.

## Consequences, compatibility and rollback

Results are bounded, short-lived and for one producer. Expiry, overflow or broker restart
can remove evidence after successful execution. Payloads contain only synthetic UUIDs,
small integers and technical retry controls. Broker permissions remain a trust boundary;
JSON does not authorize publishers. No Redis broker, domain table, business job or deployment
is added. API probes are compatible. Rollback stops producer/workers, disables opt-ins and
restores prior code/lock; let data expire and remove only reviewed exact technical resources.
No database migration or volume reset is needed.

## Validation and follow-up

Require real Redis/RabbitMQ/prefork tests for completion, worker absence/restart, retries,
exhaustion, terminal failure, publish-confirm uncertainty, isolation and cleanup. Keep strict
infrastructure-free CI. Maintainers review the RPC subclass/pool hook on dependency upgrades
and global QoS deprecation before broker upgrades. Production TLS/ACLs, HA, retention,
dead-letter/replay and domain delivery guarantees require separate authorization.
See [operations](../ASYNC_INFRASTRUCTURE.md) and [tests](../TESTING.md#fl-006-broker-validation).
