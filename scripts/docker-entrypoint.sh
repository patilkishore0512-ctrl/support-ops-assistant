#!/bin/sh
# Container start: build the ticket store and the index if they are missing, then serve the API.
set -e

DB="${TICKETS_DB_PATH:-/app/warehouse/tickets.duckdb}"

if [ ! -f "$DB" ]; then
  echo "ticket store not found, running the Part A pipeline..."
  python -m pipeline run
fi

python -m rag_service ingest --if-missing

exec uvicorn rag_service.api:app --host 0.0.0.0 --port "${PORT:-8000}"
