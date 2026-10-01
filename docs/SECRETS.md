# FL-008 secrets and configuration operations

FL-008 is a Platform foundation awaiting independent review. No identity or provider
integration is implemented. [ADR-0003](ADR/0003-secrets-configuration-boundary.md)
records the source boundary and scanner decision.

## Classification

All settings use explicit `FLEETLINK_` names. The authoritative `SECRET_FIELDS` set in
`fleetlink.core.secrets` classifies these sensitive fields:

| Variables (prefix FLEETLINK_) | Classification and reason |
| --- | --- |
| POSTGRES_USER, POSTGRES_PASSWORD | Credential pair |
| RABBITMQ_USER, RABBITMQ_PASSWORD | Credential pair |
| REDIS_USERNAME, REDIS_PASSWORD | Optional existing ACL credentials |
| OTEL_EXPORTER_OTLP_ENDPOINT | Sensitive deployment origin; credentials, query, fragment and paths are forbidden |
| ENVIRONMENT, LOG_LEVEL | Ordinary bounded operational settings |
| POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB | Ordinary connection target identifiers |
| DATABASE_ENABLED, DATABASE_POOL_SIZE, DATABASE_MAX_OVERFLOW, DATABASE_POOL_TIMEOUT, DATABASE_CONNECT_TIMEOUT, DATABASE_COMMAND_TIMEOUT | Ordinary flags and bounded resource settings |
| REDIS_ENABLED, REDIS_HOST, REDIS_PORT, REDIS_MAX_CONNECTIONS, REDIS_CONNECT_TIMEOUT, REDIS_OPERATION_TIMEOUT | Ordinary flags, targets and limits |
| CELERY_ENABLED, CELERY_QUEUE, CELERY_CONCURRENCY | Ordinary bounded technical worker configuration |
| RABBITMQ_HOST, RABBITMQ_AMQP_PORT, RABBITMQ_MANAGEMENT_PORT, RABBITMQ_VHOST | Ordinary target identifiers; management port belongs to Compose |
| BROKER_CONNECT_TIMEOUT, BROKER_OPERATION_TIMEOUT | Ordinary bounded transport limits |
| TELEMETRY_ENABLED, OTEL_EXPORT_TIMEOUT_SECONDS, OTEL_SAMPLE_RATIO | Ordinary bounded telemetry controls |
| BROKER_TESTS | Test-only explicit integration gate; no credential |
| GITLEAKS_BIN | Make-only scanner executable path; ordinary developer configuration |

Identifiers must never be used to carry passwords or tokens. Hosts and database/vhost
names are not automatically private; assess that classification before changing their use.
Future API tokens, OAuth secrets, signing material, recovery secrets, webhooks, payment
credentials and provider credentials will need explicit classification in their owning task.
None are added here.

## Source and validation

`SecretSource.resolve(name)` synchronously returns `SecretStr | None` for an explicit
variable name. `EnvironmentSecretSource` snapshots only classified variables from the
process environment or an explicitly supplied mapping. It reads no file and opens no
connection. `Settings(secret_source=source)` injects a source; explicit constructor values
win, then source values, then existing settings defaults. An injected source never falls back to ambient secrets. Ordinary configuration retains
pydantic-settings environment behavior. FleetLink does not discover `.env` implicitly.
Avoid passing Pydantic `_env_file`/secret-directory escape hatches; those are outside the
supported configuration interface. Explicit `uv run --env-file .env` remains supported.

Local/test defaults remain compatible with the development Compose placeholders.
Empty enabled PostgreSQL/RabbitMQ credential pairs fail explicitly. Staging/production
cannot enable those resources with the shipped development credential defaults. Redis
without ACL credentials remains supported; enabling telemetry still requires a valid origin.
Disabled resources need no credentials. Field names and validation constraints remain in
errors; raw rejected inputs and exception context are removed from structured validation
errors as well as error strings. Non-secret configuration is available in
`Settings.diagnostic_configuration()` with sensitive fields replaced by `<redacted>`.
No configuration variable is renamed.

## Representation and diagnostic boundaries

Existing frozen Pydantic settings and `SecretStr` are reused. Secret repr/str and JSON
serialization mask values; sensitive fields are omitted from settings repr. Secrets are
unwrapped only for infrastructure clients/exporters, credential validation and the diagnostic redactor.
`connection_target()` returns a host/port target without credentials. Never log raw URLs,
SQLAlchemy URL fields, Celery configuration, pool/client internals or environment dumps.

An immutable per-settings `Redactor` replaces known credential strings and URL-encoded
variants. No process-global credential registry exists. HTTP middleware explicitly owns
request context; lifespan and worker execution own their contexts and reset them on exit.
Correlation identifiers matching known credentials are replaced with a generated UUID.
The JSON formatter never interpolates arbitrary messages, settings or exception objects:
registered event names survive; other messages become `application_event`. Extras are
limited to numeric status/duration and reviewed fixed exception-type names (unknown types become `operation_error`). Trace/span IDs
and ordinary correlation IDs remain available. This deliberately reduces exception-type
and unregistered-event detail; add reviewed fixed event names to the central allowlist.
Worker library messages remain `worker_runtime_event`, with tracebacks and payloads omitted.

Existing transport-specific database/Redis errors remain sanitized with suppressed
exception chaining. Broker connection entry/exit and producer/worker cleanup also sanitize
known transport errors. Programming exceptions still propagate; do not print their raw
tracebacks in shared diagnostics. Suppressed context is still present in Python exception
objects: trusted debuggers/memory inspection can reveal it; logging does not serialize it.

HTTP seven-field Problems, probe responses and readiness semantics remain stable. Error
details omit framework/driver inputs. Exception headers are restricted to validated `Allow`
and numeric `Retry-After`; arbitrary exception headers no longer pass through. This is a
security restriction on the technical error boundary, not a new product endpoint.

## Telemetry

FL-007's bounded names/attributes, no request bodies/headers/query strings/SQL parameters,
no baggage/tracestate and no automatic exception events remain in place. Known credentials
are scrubbed from operation names/attributes, service resource names and task labels at the
FleetLink wrapper boundary. Metric names are fixed. Exporter failures use existing sanitized
logging. Export stays opt-in and never affects readiness. Explicit SDK span/event/instrument
calls by future application code bypass these wrappers: they require review and tests before
use. Do not put credentials in any name, attribute, event or metric label.

## Lifetime and rotation

Secrets resolve once per settings construction. Each application lifespan owns lazy database
and Redis pools; the worker/producer owns broker connections. These clients capture plaintext
credentials at construction. Changing the process environment does not update a settings
snapshot or any existing client. There is no polling, refresh loop or automatic rotation.

To adopt rotated credentials: provision the new credential with overlapping validity,
construct new settings and new resources, stop accepting work on old resources, drain
transactions/tasks under existing deadlines, close old pools/connections and retire the old
credential after completion. Restart API/worker processes using new configuration when no
live resource replacement orchestration exists. Do not mutate frozen settings or replace a
credential in an in-use pool. Future providers must add explicit resolution before resource
construction, safe provider errors, version/lease semantics and orchestration for replacement;
network resolution must not happen implicitly in Settings. Providers may implement the narrow
protocol using already-resolved snapshots. Resource recreation remains necessary.

## Development and CI scanning

`.env.example` contains development-only placeholders; `.env` stays ignored. Never reuse
placeholders outside local/test. CI uses the MIT-licensed Gitleaks CLI 8.30.1 release,
verified against SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`
for its Linux x64 archive. The standalone CLI avoids action license/service dependencies.
The upstream release and release maintenance were checked during FL-008 selection:
[releases](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1),
[license](https://github.com/gitleaks/gitleaks/blob/v8.30.1/LICENSE).
It scans full fetched Git history and the working tree with redacted output; no allowlist
or excluded directory was added. Maintainers must review release/security updates and checksums.
No scanner can establish absence of all secrets or detect all low-entropy passwords.

Install that release for your platform after checksum verification, then run:

```sh
gitleaks version  # must report 8.30.1
make secret-scan
make api-test-secrets
```

The equivalent commands are `gitleaks git --redact --no-banner .` and
`gitleaks dir --redact --no-banner .`. Tests generate unmistakably fake, unique in-memory
sentinels; no plausible provider keys are fixtures. Future scanner false positives require
an individually reviewed fingerprint or exact fixture rule, never a directory exclusion.
The scan needs no secrets, cloud account, provider SDK or production connectivity.

## Incidents, limitations and rollback

If a credential is exposed, revoke/rotate it through its owner, contain access, preserve
restricted audit evidence and investigate affected history/artifacts/logs. Do not paste the
credential into an issue or normal logs. Coordinate any history remediation separately;
FL-008 does not rewrite history. Redaction is defense in depth, not encryption or secure
erasure. Environment values and client plaintext necessarily remain in process memory.
Raw SDK/library debug dumps, third-party handlers, explicit exception-context traversal,
crash/core dumps and arbitrary transformations (base64, hashes, partial strings) are outside
the tested boundary. No global monkey patch is installed. Keep third-party verbose/debug
output disabled and restrict process/debug access. Local Compose remains development-only.

Rollback is a reviewed revert of the FL-008 commit followed by API/worker restart. There is
no schema/data migration or lockfile change. Reverting removes enhanced validation, diagnostic
and scanner protections; do not use rollback to reuse compromised credentials. Health/readiness,
transaction ownership, RabbitMQ-only task routing and the value-42 probe are unchanged.
Managed providers, automated rotation, authentication, signing, cloud/deployment infrastructure,
production TLS, payments and all Phase 1/FL-009 work remain deferred.
