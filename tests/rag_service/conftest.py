"""Shared fixtures: a tiny knowledge base, a tiny ticket store, and offline model settings."""

import json
from pathlib import Path

import duckdb
import pytest

KB_DOCS = {
    "docs/tutorial/cors.md": """# CORS (Cross-Origin Resource Sharing) { #cors }

CORS refers to situations when a frontend running in a browser communicates with a backend in a different origin.

## Use `CORSMiddleware` { #use-corsmiddleware }

You can configure it in your FastAPI application using the `CORSMiddleware`.
Create a list of allowed origins and pass it as `allow_origins`.

{* ../../docs_src/cors/tutorial001.py *}

/// tip

Use explicit origins instead of the wildcard when credentials are involved.

///
""",
    "docs/deployment/docker.md": """# FastAPI in Containers - Docker

## Replication - Number of Processes

When running on Kubernetes you would normally run a single Uvicorn process per container.
Kubernetes handles replication at the cluster level instead of using multiple workers.
""",
    "docs/tutorial/security/oauth2-jwt.md": """# OAuth2 with Password (and hashing), Bearer with JWT tokens

## Hash and verify the passwords

Create a PasswordHash instance with recommended settings. It will be used for hashing and verifying passwords.
Never store plaintext passwords; store the hash and compare hashes on login.
""",
    "docs/tutorial/background-tasks.md": """# Background Tasks

You can define background tasks to be run after returning a response.
This is useful for operations that need to happen after a request, like sending an email notification.
""",
}


@pytest.fixture(scope="session")
def kb_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("kb")
    manifest = []
    for rel, text in KB_DOCS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        manifest.append({"path": rel, "url": f"https://example.test/{rel}", "last_updated": "2026-07-01"})
    manifest[1]["last_updated"] = "2025-01-15"
    (root / "manifest.json").write_text(json.dumps({"documents": manifest}))
    return root


@pytest.fixture(scope="session")
def ticket_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "tickets.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA clean; CREATE SCHEMA curated; CREATE SCHEMA raw;")
    con.execute("""CREATE TABLE clean.tickets AS SELECT * FROM (VALUES
        ('TKT-000001','CUST-0001',TIMESTAMP '2026-09-01 10:00',TIMESTAMP '2026-09-02 10:00','resolved','high','billing','AGT-001','Mailed jane@example.com','email'),
        ('TKT-000002','CUST-0002',TIMESTAMP '2026-09-03 10:00',TIMESTAMP '2026-09-03 12:00','open','urgent','api',NULL,NULL,'chat'),
        ('TKT-000003','CUST-0003',TIMESTAMP '2026-09-04 10:00',TIMESTAMP '2026-09-05 10:00','open','high','billing','AGT-002',NULL,'web')
        ) t(ticket_id, customer_id, created_at, updated_at, status, priority, product, agent_id, resolution_text, channel)""")
    con.execute("CREATE TABLE clean.agents AS SELECT * FROM (VALUES ('AGT-001','Agent 001','Tier1'),('AGT-002','Agent 002','Tier2')) t(agent_id, agent_name, team)")
    con.execute("CREATE TABLE clean.customers AS SELECT 'CUST-0001' AS customer_id, 'Secret Name' AS customer_name")
    con.execute("CREATE TABLE raw.tickets AS SELECT * FROM clean.tickets")
    con.execute("""CREATE VIEW curated.avg_resolution_by_product AS
        SELECT 'billing' AS product, 1 AS resolved_tickets, 24.0 AS avg_resolution_hours,
               24.0 AS median_resolution_hours, 24.0 AS p90_resolution_hours""")
    con.execute("""CREATE VIEW curated.backlog_by_priority_week AS SELECT * FROM (VALUES
        (DATE '2026-09-28','urgent',5,2,1),(DATE '2026-09-28','high',7,3,2)) t(week_start, priority, open_backlog_at_week_end, created_in_week, resolved_in_week)""")
    con.execute("CREATE VIEW curated.ticket_resolution AS SELECT ticket_id, product, priority, status, created_at, updated_at AS resolved_at, 24.0 AS resolution_hours FROM clean.tickets")
    con.close()
    return path


@pytest.fixture
def offline_env(monkeypatch, kb_dir, ticket_db, tmp_path):
    """Environment for building an Assistant fully offline."""
    monkeypatch.setenv("KB_DIR", str(kb_dir))
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("TICKETS_DB_PATH", str(ticket_db))
    monkeypatch.setenv("EMBEDDING_BACKEND", "lsa")
    monkeypatch.setenv("RERANKER_BACKEND", "none")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MIN_CONFIDENCE", "0.35")
    return tmp_path
