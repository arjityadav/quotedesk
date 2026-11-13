.PHONY: install check lint fmt type test test-db db-up db-down seed clean

install:
	uv sync

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy

test:
	uv run pytest

check: lint type test

db-up:
	docker compose -f deploy/compose/compose.yaml up -d db

db-down:
	docker compose -f deploy/compose/compose.yaml down

test-db:
	DATABASE_URL=postgresql://quotedesk:quotedesk@localhost:5432/quotedesk uv run pytest -m db

seed:
	DATABASE_URL=postgresql://quotedesk:quotedesk@localhost:5432/quotedesk uv run quotedesk seed

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache data/bmecat/*.xml
