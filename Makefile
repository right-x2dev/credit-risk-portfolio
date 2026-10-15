# Unix/macOS convenience targets (Windows: use scripts/*.ps1 — same steps).
PY ?= .venv/bin/python
CLI = $(PY) -m credit_risk.cli

.PHONY: bootstrap ingest marts train test run-all synthetic clean-db

bootstrap:
	uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements.lock && uv pip install --python .venv -e . --no-deps
	docker compose up -d --wait postgres
	$(CLI) init-db

ingest:
	$(CLI) ingest

marts:
	$(CLI) build-staging && $(CLI) build-features && $(CLI) eda && $(CLI) split

train:
	$(CLI) train && $(CLI) score && $(CLI) build-mart && $(CLI) export-bi && $(CLI) docs

test:
	$(PY) -m ruff check src tests && $(PY) -m pytest tests -q

run-all: bootstrap ingest marts train test

synthetic:
	$(CLI) make-synthetic --out data/synthetic

clean-db:  ## destructive: drops all project schemas (manual use only)
	docker compose exec -T postgres psql -U $$POSTGRES_USER -d $$POSTGRES_DB -f - < sql/99_teardown/999_drop_all.sql
