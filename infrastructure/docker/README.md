# Local infrastructure (FL-003)

This optional development stack provisions PostgreSQL/PostGIS, Redis and RabbitMQ.
The API still starts and passes its tests without these services; `/health` and
`/ready` remain application-only. No application clients, domain tables, workers,
queues or business exchanges are configured. PostGIS may create its own extension
objects; RabbitMQ creates its standard built-in broker objects.

## Quick start

Use Docker Engine and Docker Compose v2.20+ (or v5), Bash, GNU `timeout`, and
optionally Make. These tools are available in the project Linux Codespace.
On macOS install GNU coreutils and expose its `timeout` command on PATH.
From the repository root, optionally copy `.env.example` to `.env` and change the
development passwords. Do not overwrite an existing `.env`.

```sh
make infra-config
make infra-up
make infra-status
make infra-check
make infra-down
```

`infra-up` waits for healthy services, and repeated starts/checks are safe.
`infra-config` validates without starting services or printing interpolated secrets.
`infra-check` starts the stack, performs authenticated TCP SQL, enables PostGIS if
needed, calls `PostGIS_Full_Version()` and a spatial function, checks Redis PING,
and checks RabbitMQ application readiness, alarms and listener connectivity.
It leaves services running for development, including after failure for inspection.

For automatic shutdown on success, failure or interruption (volumes preserved):

```sh
./infrastructure/docker/validate.sh check --cleanup
```

This stops the whole FleetLink stack, including services already running before
validation; do not use it concurrently with other work using the stack.
All Make targets invoke this script. It works from any current directory.
Each Docker operation is bounded: 15 seconds for availability checks, 60 seconds
for probes/status/shutdown, and 600 seconds for startup including pulls, with a
180-second Compose health deadline. Forced termination adds at most 10 seconds
per command. Failures return nonzero and print status; cleanup failure is reported.

## Configuration and ports

The script explicitly loads root `.env` if present, otherwise `.env.example`.
Exported shell values override the file; missing/empty values use Compose defaults.
The API's existing environment convention is unchanged. No connection strings are
introduced into application settings.

| Variable | Development default | Purpose |
| --- | --- | --- |
| `FLEETLINK_POSTGRES_DB` | `fleetlink_dev` | Initial development database |
| `FLEETLINK_POSTGRES_USER` | `fleetlink_dev` | Initial database administrator |
| `FLEETLINK_POSTGRES_PASSWORD` | `development-only-postgres` | Placeholder database password |
| `FLEETLINK_POSTGRES_PORT` | `5432` | Host PostgreSQL port |
| `FLEETLINK_REDIS_PORT` | `6379` | Host Redis port |
| `FLEETLINK_RABBITMQ_USER` | `fleetlink_dev` | Initial broker administrator |
| `FLEETLINK_RABBITMQ_PASSWORD` | `development-only-rabbitmq` | Placeholder broker password |
| `FLEETLINK_RABBITMQ_AMQP_PORT` | `5672` | Host AMQP port |
| `FLEETLINK_RABBITMQ_MANAGEMENT_PORT` | `15672` | Host management HTTP port |

Ports bind only to the Docker host's `127.0.0.1`. Internal ports are always 5432,
6379, 5672 and 15672. The management UI at <http://127.0.0.1:15672> provides local
broker diagnostics; sign in with the configured RabbitMQ credentials.
Redis retains its upstream local development authentication behavior (no password);
it is reachable only through the loopback publication and dedicated Docker network.
Never reuse these credentials or this unencrypted development topology in production.
Only trusted local users/containers should access this daemon/network. Docker admins
can inspect container environments. Do not share rendered Compose config or raw logs
without checking them for sensitive values.

## Persistence and reset

The project is explicitly `fleetlink-local`, with network `fleetlink-local-network`.
Named volumes are `fleetlink-local-postgres-data`, `fleetlink-local-redis-data` and
`fleetlink-local-rabbitmq-data`. PostgreSQL 18 uses `/var/lib/postgresql`; Redis uses
AOF in `/data`; RabbitMQ uses `/var/lib/rabbitmq` with a stable hostname so its node
identity survives container recreation. There are no host bind mounts.

`make infra-down` removes containers/network but preserves all three volumes.
Services use `unless-stopped` restart behavior. Volumes are development persistence,
not backups or a durability guarantee.

**`make infra-reset` is destructive:** it stops this stack and deletes all three
volumes and their local data. Use it only when that data is disposable. A subsequent
`make infra-up` creates a clean stack. There is no recovery without your own backup.
Changing initial PostgreSQL/RabbitMQ credentials in `.env` does not update an
existing volume; use native administration or explicitly reset disposable data.
Do not change PostgreSQL major versions against an existing volume; plan an export
and restore. Reverting these files alone does not downgrade stored data.

## Codespaces and troubleshooting

Docker-outside-of-Docker runs services on the daemon host, not inside the development
container. Named volumes avoid paths that differ between those environments.
Validation uses `docker compose exec` and therefore needs no host SQL/Redis/AMQP
clients and does not assume devcontainer `localhost` reaches daemon host ports.
Loopback bindings intentionally remain private. Codespaces port forwarding may not
reach daemon-host loopback from the devcontainer; the native checks still work.
For the UI, use an authorized tunnel to the daemon-host loopback if the Ports panel
cannot reach it; keep forwarding private. Do not change bindings to `0.0.0.0` just
to bypass this boundary. Host browser access depends on the environment's forwarding.

- Docker unavailable: check `docker info`, daemon/socket access and the Docker feature.
- Unsupported flags: update Compose; `up --wait --wait-timeout` is required.
- Port in use: change the relevant host port in `.env`, then rerun `infra-up`.
- Pull failure: check registry/network access, disk space and architecture support.
  The selected upstream PostGIS Debian image supports amd64; ARM is not validated.
- Unhealthy services: run `make infra-status`, inspect logs below, check memory/disk,
  credentials and volume compatibility, then rerun validation. Do not reset valuable data.
- Interrupted cleanup: run `make infra-down`; volumes remain available.
- Multiple checkouts share these fixed names: run only one FleetLink local stack per daemon.

For advanced inspection from the repository root (use `.env.example` if no `.env`):

```sh
docker compose --project-name fleetlink-local --project-directory . --env-file .env \
  -f infrastructure/docker/compose.yaml logs --tail 100
```

## Image selection and maintenance

Selected from upstream stable image listings on 2026-09-26:

| Image | Rationale and upstream source |
| --- | --- |
| `postgis/postgis:18-3.6` | Maintained PostGIS project image, PostgreSQL 18/PostGIS 3.6; [supported versions](https://github.com/postgis/docker-postgis) |
| `redis:8.10.2-trixie` | Stable Redis official image; [official tags](https://github.com/docker-library/official-images/blob/master/library/redis) |
| `rabbitmq:4.3.6-management` | Stable official broker image with local diagnostics UI; [official tags](https://github.com/docker-library/official-images/blob/master/library/rabbitmq) |

All three also have immutable registry digest pins in `compose.yaml`. Review tags
and digests together for security updates, then rerun validation and persistence
checks. Digest pinning prevents silent fixes as well as silent changes. No image
vulnerability scan is claimed by the native readiness checks.

These are the already accepted architecture services. Host installations would
require more platform-specific setup; managed/cloud services are outside FL-003.
No application dependency is added. PostgreSQL uses the PostgreSQL license,
PostGIS GPL-2.0-or-later, Redis 8 offers AGPLv3/RSALv2/SSPLv1 licensing choices, and
RabbitMQ uses MPL-2.0; bundled image components have their own licenses. Review
licensing before redistribution or production adoption. Operational costs are local
CPU, memory, disk, image downloads and responsibility for reviewed patch updates.
