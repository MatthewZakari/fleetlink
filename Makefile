.PHONY: help api-install api-run api-test api-lint api-typecheck mobile-get mobile-analyze mobile-test
help:
	@echo "api-install     Install locked Python dependencies"
	@echo "api-run         Run the bootstrap API locally"
	@echo "api-test        Run backend tests"
	@echo "api-lint        Check backend lint and formatting"
	@echo "api-typecheck   Check backend types"
	@echo "mobile-get      Resolve Flutter dependencies"
	@echo "mobile-analyze  Analyze Flutter application"
	@echo "mobile-test     Run Flutter widget tests"

api-install:
	uv sync --project apps/api --locked
api-run:
	uv run --project apps/api --locked uvicorn fleetlink.main:create_app --factory --no-access-log
api-test:
	uv run --project apps/api --locked pytest apps/api/tests
api-lint:
	uv run --project apps/api --locked ruff check apps/api
	uv run --project apps/api --locked ruff format --check apps/api
api-typecheck:
	cd apps/api && uv run --locked mypy src tests
mobile-get:
	cd apps/mobile && flutter pub get
mobile-analyze:
	cd apps/mobile && flutter analyze
mobile-test:
	cd apps/mobile && flutter test
