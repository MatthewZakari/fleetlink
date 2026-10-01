.PHONY: help api-install api-run api-test api-lint api-typecheck mobile-get mobile-analyze mobile-test
help:
	@echo "infra-config    Validate local Compose configuration"
	@echo "infra-up        Start infrastructure and wait for health"
	@echo "infra-status    Show infrastructure status"
	@echo "infra-check     Start and validate infrastructure"
	@echo "infra-down      Stop infrastructure; preserve data"
	@echo "infra-reset     DESTRUCTIVE: stop and delete infrastructure data"
	@echo "api-install     Install locked Python dependencies"
	@echo "api-run         Run the bootstrap API locally"
	@echo "api-test        Run infrastructure-free backend tests"
	@echo "api-test-secrets Run infrastructure-free FL-008 security regressions"
	@echo "secret-scan     Scan Git history and working tree with Gitleaks 8.30.1"
	@echo "api-test-observability Run infrastructure-free OpenTelemetry contracts"
	@echo "api-test-db     Run explicit isolated PostgreSQL tests"
	@echo "api-worker      Start dedicated technical Celery worker (explicit opt-in)"
	@echo "api-test-tasks  Run infrastructure-free Redis/Celery tests"
	@echo "api-test-broker Run explicit real Redis/RabbitMQ/worker tests"
	@echo "api-task-smoke  Publish technical probe and wait for worker completion"
	@echo "api-db-test-setup  Create fresh fleetlink_test_fl005 with PostGIS"
	@echo "api-db-test-drop   DESTRUCTIVE: drop only fleetlink_test_fl005"
	@echo "api-db-upgrade  Migrate explicitly configured database to head"
	@echo "api-db-current  Show explicitly configured database revision"
	@echo "api-db-history  Show migration history (no connection)"
	@echo "api-db-downgrade-base  Explicit rollback to base"
	@echo "api-lint        Check backend lint and formatting"
	@echo "api-typecheck   Check backend types"
	@echo "mobile-get      Resolve Flutter dependencies"
	@echo "mobile-analyze  Analyze Flutter application"
	@echo "mobile-test     Run Flutter widget tests"

api-install:
	uv sync --project apps/api --locked
api-run:
	uv run --project apps/api --locked uvicorn fleetlink.main:create_app --factory --no-access-log --no-server-header --no-proxy-headers
api-test:
	uv run --project apps/api --locked pytest apps/api/tests
.PHONY: api-test-observability
api-test-observability:
	uv run --project apps/api --locked pytest apps/api/tests/test_observability.py
api-lint:
	uv run --project apps/api --locked ruff check apps/api
	uv run --project apps/api --locked ruff format --check apps/api
api-typecheck:
	cd apps/api && uv run --locked mypy src tests tests_db tests_broker migrations
mobile-get:
	cd apps/mobile && flutter pub get
mobile-analyze:
	cd apps/mobile && flutter analyze
mobile-test:
	cd apps/mobile && flutter test

.PHONY: infra-config infra-up infra-status infra-check infra-down infra-reset
infra-config infra-up infra-status infra-check infra-down infra-reset:
	./infrastructure/docker/validate.sh $(patsubst infra-%,%,$@)

.PHONY: api-test-db api-db-test-setup api-db-test-drop api-db-upgrade api-db-current api-db-history api-db-downgrade-base
api-test-db:
	uv run --project apps/api --locked pytest apps/api/tests_db
api-db-test-setup:
	./infrastructure/docker/test-database.sh setup fleetlink_test_fl005
api-db-test-drop:
	./infrastructure/docker/test-database.sh drop fleetlink_test_fl005
api-db-upgrade:
	uv run --project apps/api --locked alembic -c apps/api/alembic.ini upgrade head
api-db-current:
	uv run --project apps/api --locked alembic -c apps/api/alembic.ini current
api-db-history:
	uv run --project apps/api --locked alembic -c apps/api/alembic.ini history
api-db-downgrade-base:
	uv run --project apps/api --locked alembic -c apps/api/alembic.ini downgrade base

.PHONY: api-worker api-test-tasks api-test-broker api-task-smoke
api-worker:
	uv run --project apps/api --locked python -m fleetlink.worker
api-test-tasks:
	uv run --project apps/api --locked pytest apps/api/tests/test_broker.py
api-test-broker:
	FLEETLINK_BROKER_TESTS=1 timeout --kill-after=10s 180s uv run --project apps/api --locked pytest apps/api/tests_broker
api-task-smoke:
	uv run --project apps/api --locked python -m fleetlink.technical_smoke

FLEETLINK_GITLEAKS_BIN ?= gitleaks
.PHONY: api-test-secrets secret-scan
api-test-secrets:
	uv run --project apps/api --locked pytest apps/api/tests/test_secrets.py
secret-scan:
	@test "$$($(FLEETLINK_GITLEAKS_BIN) version)" = "8.30.1"
	$(FLEETLINK_GITLEAKS_BIN) git --redact --no-banner .
	$(FLEETLINK_GITLEAKS_BIN) dir --redact --no-banner .
