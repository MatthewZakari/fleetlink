#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
action=${1:-check}
cleanup=${2:-}
case "$action" in
    config|up|status|check|down|reset) ;;
    *) echo "Usage: $0 {config|up|status|check|down|reset} [--cleanup]" >&2; exit 2 ;;
esac
if [[ -n "$cleanup" && ( "$action" != check || "$cleanup" != --cleanup ) ]]; then
    echo '--cleanup is supported only with check (preserves volumes).' >&2
    exit 2
fi
for tool in docker timeout; do
    command -v "$tool" >/dev/null || { echo "Required tool unavailable: $tool" >&2; exit 1; }
done
env_file="$root/.env.example"
[[ ! -f "$root/.env" ]] || env_file="$root/.env"
compose=(docker compose --project-name fleetlink-local --project-directory "$root"
    --env-file "$env_file" -f "$root/infrastructure/docker/compose.yaml")
# Probes never consume input. Compose exec -T still attaches stdin; under timeout
# a terminal read can stop its background process group with SIGTTIN. Supply EOF.
run() { timeout --kill-after=10s 60s "${compose[@]}" "$@" </dev/null; }
timeout --kill-after=5s 15s docker compose version
run config --quiet
if [[ "$action" == config ]]; then
    echo 'Compose configuration valid (values are not printed to protect credentials).'
    exit 0
fi
timeout --kill-after=5s 15s docker info >/dev/null
started=false
finish() {
    result=$?
    trap - EXIT
    if (( result != 0 )); then
        echo "Infrastructure $action failed (exit $result). Container status:" >&2
        run ps --all >&2 || true
        echo 'Inspect local logs with docker compose using the options documented in README.md.' >&2
    fi
    if [[ "$cleanup" == --cleanup && "$started" == true ]]; then
        run down --timeout 20 || { echo 'Cleanup failed; run make infra-down.' >&2; result=1; }
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
case "$action" in
    status) run ps --all ;;
    down) run down --timeout 20 ;;
    reset)
        echo 'DESTRUCTIVE: removing all FleetLink local infrastructure volumes.'
        run down --volumes --timeout 20 ;;
    up|check)
        started=true
        # The outer deadline also bounds image pulls and Docker daemon calls.
        timeout --kill-after=10s 600s "${compose[@]}" up --detach --wait --wait-timeout 180
        if [[ "$action" == up ]]; then exit 0; fi
        # The network hostname avoids the image's trusted loopback authentication.
        echo 'Checking authenticated PostgreSQL connectivity and PostGIS...'
        run exec -T postgres sh -ec '
            export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
            export PGOPTIONS="-c statement_timeout=10000 -c lock_timeout=5000"
            psql -X -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
                -c "SELECT current_database(), version();" \
                -c "CREATE EXTENSION IF NOT EXISTS postgis;" \
                -c "SELECT PostGIS_Full_Version(), ST_AsText(ST_SetSRID(ST_MakePoint(0, 0), 4326));"
        '
        echo 'Checking Redis PING...'
        run exec -T redis sh -ec 'test "$(redis-cli ping)" = PONG'
        echo 'Checking RabbitMQ application, alarms and listeners...'
        run exec -T rabbitmq rabbitmq-diagnostics -q check_running
        run exec -T rabbitmq rabbitmq-diagnostics -q check_local_alarms
        run exec -T rabbitmq rabbitmq-diagnostics -q check_port_connectivity
        echo 'Infrastructure validation passed.' ;;
esac
