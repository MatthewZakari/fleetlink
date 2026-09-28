#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ $# != 2 || "$2" != fleetlink_test_fl005 ]]; then
    echo 'Usage: test-database.sh {setup|drop} fleetlink_test_fl005' >&2
    exit 2
fi
env_file="$root/.env.example"
[[ ! -f "$root/.env" ]] || env_file="$root/.env"
compose=(docker compose --project-name fleetlink-local --project-directory "$root"
    --env-file "$env_file" -f "$root/infrastructure/docker/compose.yaml")
case "$1" in
    setup)
        timeout --kill-after=5s 60s "${compose[@]}" exec -T postgres sh -ec '
            export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
            export PGOPTIONS="-c statement_timeout=10000 -c lock_timeout=5000"
            psql -X -h postgres -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
                -c "CREATE DATABASE fleetlink_test_fl005 TEMPLATE template0;"
            psql -X -h postgres -U "$POSTGRES_USER" -d fleetlink_test_fl005 -v ON_ERROR_STOP=1 \
                -c "CREATE EXTENSION postgis;"
        ' </dev/null ;;
    drop)
        echo 'DESTRUCTIVE: dropping only fleetlink_test_fl005.'
        timeout --kill-after=5s 60s "${compose[@]}" exec -T postgres sh -ec '
            export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
            export PGOPTIONS="-c statement_timeout=10000 -c lock_timeout=5000"
            psql -X -h postgres -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
                -c "DROP DATABASE fleetlink_test_fl005;"
        ' </dev/null ;;
    *) echo 'Expected setup or drop' >&2; exit 2 ;;
esac
