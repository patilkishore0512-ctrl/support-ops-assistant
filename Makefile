PY ?= python
PORT ?= 8000

.PHONY: help setup setup-models generate pipeline rerun report reset ingest serve ask eval eval-offline test docker-build docker-up smoke all

help:            ## list commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

setup:           ## install dependencies and the project
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m pip install -e .

setup-models:    ## optional: neural embeddings + cross-encoder (CPU torch)
	$(PY) -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-models.txt

# ---------- Part A: data pipeline ----------
generate:        ## create synthetic ticket files in data/
	$(PY) -m pipeline generate --seed 42

pipeline:        ## ingest every daily file (already-loaded files are skipped)
	$(PY) -m pipeline run

rerun:           ## prove idempotency: reprocess day 2, counts stay identical
	$(PY) -m pipeline run --day 2026-10-02 --force

report:          ## print curated outputs + DQ summary, write CSVs to outputs/pipeline/
	$(PY) -m pipeline report

reset:           ## delete the local warehouse (ticket store and index)
	$(PY) -m pipeline reset
	rm -rf warehouse/index

# ---------- Part B: RAG service ----------
ingest:          ## parse, chunk, embed and index the knowledge base
	$(PY) -m rag_service ingest

serve:           ## run the API on http://localhost:$(PORT) (docs at /docs)
	$(PY) -m rag_service serve --port $(PORT)

ask:             ## ask one question, e.g. make ask Q="How do I enable CORS?"
	$(PY) -m rag_service ask "$(Q)"

# ---------- Part C: evaluation ----------
eval:            ## evaluate every configuration that can run here
	$(PY) evaluation/run_eval.py

eval-offline:    ## evaluate BM25 + LSA configurations (no model downloads)
	$(PY) evaluation/run_eval.py --configs offline --out outputs/eval-offline

# ---------- Docker ----------
docker-build:    ## build the image (with models)
	docker build -t support-ops-assistant .

docker-up:       ## build and run with docker compose
	docker compose up --build

smoke:           ## smoke-test a running service
	$(PY) scripts/smoke_test.py http://localhost:$(PORT)

# ---------- quality ----------
test:            ## run all tests
	$(PY) -m pytest -q

all: generate pipeline report ingest test   ## everything from scratch: data, ticket store, index, tests
