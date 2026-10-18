PY ?= python

.PHONY: help setup generate pipeline rerun report test reset all

help:           ## list commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup:          ## install dependencies and the project (editable)
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install -e .

# ---------- Part A: data pipeline ----------
generate:       ## create synthetic ticket files in data/
	$(PY) -m pipeline generate --seed 42

pipeline:       ## ingest every daily file (already-loaded files are skipped)
	$(PY) -m pipeline run

rerun:          ## prove idempotency: reprocess day 2, counts stay identical
	$(PY) -m pipeline run --day 2026-10-02 --force

report:         ## print curated outputs + DQ summary, write CSVs to outputs/pipeline/
	$(PY) -m pipeline report

reset:          ## delete the local warehouse
	$(PY) -m pipeline reset

# ---------- quality ----------
test:           ## run all tests
	$(PY) -m pytest -q

all: generate pipeline report test   ## full Part A run from scratch
