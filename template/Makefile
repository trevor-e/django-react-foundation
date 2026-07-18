.PHONY: install dev dev-up dev-down dev-status dev-logs dev-backend dev-frontend \
	test test-backend test-frontend lint typecheck \
	migrate makemigrations gen-api-types check-api-schema \
	shell manage test-db-up test-db-down dev-db-up

BACKEND := cd backend && uv run
FRONTEND := cd frontend && pnpm

install:
	cd backend && uv sync
	cd frontend && pnpm install

dev-db-up:
	cd backend && docker compose -f docker-compose.dev.yml up -d

# Granian in dev too — same ASGI serving stack as prod (blueprint §11a).
dev-backend: dev-db-up
	$(BACKEND) granian --interface asginl config.asgi:application --port 8000 --reload --workers-kill-timeout 3 --reload-ignore-dirs .ruff_cache --reload-ignore-dirs .pytest_cache --reload-ignore-patterns '\.tmp\.'

dev-frontend:
	$(FRONTEND) run dev

dev:
	./scripts/dev.sh

# Background dev stack for coding agents (foreground `make dev` is for humans).
dev-up:
	./scripts/devctl.sh up

dev-down:
	./scripts/devctl.sh down

dev-status:
	./scripts/devctl.sh status

dev-logs:
	./scripts/devctl.sh logs

test: test-backend test-frontend

test-db-up:
	cd backend && docker compose -f docker-compose.test.yml up -d
	cd backend && until docker compose -f docker-compose.test.yml exec -T postgres-test pg_isready -U __PROJECT___test > /dev/null 2>&1; do sleep 1; done

test-db-down:
	cd backend && docker compose -f docker-compose.test.yml down

test-backend: test-db-up
	$(BACKEND) pytest
	$(MAKE) test-db-down

test-frontend:
	$(FRONTEND) run typecheck
	$(FRONTEND) run lint

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	$(FRONTEND) run lint

typecheck:
	cd backend && uv run pyrefly check
	$(FRONTEND) run typecheck

migrate:
	$(BACKEND) python manage.py migrate

makemigrations:
	$(BACKEND) python manage.py makemigrations

gen-api-types:
	$(BACKEND) python manage.py export_api_schema
	$(FRONTEND) run gen:types

check-api-schema:
	$(BACKEND) python manage.py export_api_schema --check

shell:
	$(BACKEND) python manage.py shell

manage:
	$(BACKEND) python manage.py $(CMD)
