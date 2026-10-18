"""
DuckDB storage: schemas, DDL, schema-drift handling, and the upsert.

Layers
  raw.tickets              every row from every file, as strings, plus load metadata (append-only)
  clean.ticket_versions    every valid version of a ticket, keyed (ticket_id, updated_at)
  clean.tickets            current state, one row per ticket (upsert, newest updated_at wins)
  clean.tickets_quarantine rows that failed an error-level check, with the reasons and the raw record
  clean.customers/agents   reference data used by referential checks
  curated.*                views for reporting (see curated.py)
  meta.*                   ingest log, data-quality results, schema changes
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from pipeline.config import CORE_COLUMNS

RAW_META_COLUMNS = ["_batch_id", "_source_file", "_row_number", "_loaded_at"]

DDL = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS raw.tickets (
    ticket_id VARCHAR, customer_id VARCHAR, created_at VARCHAR, updated_at VARCHAR,
    status VARCHAR, priority VARCHAR, product VARCHAR, agent_id VARCHAR, resolution_text VARCHAR,
    _batch_id VARCHAR, _source_file VARCHAR, _row_number INTEGER, _loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clean.ticket_versions (
    ticket_id VARCHAR NOT NULL, customer_id VARCHAR, created_at TIMESTAMP, updated_at TIMESTAMP NOT NULL,
    status VARCHAR, priority VARCHAR, product VARCHAR, agent_id VARCHAR, resolution_text VARCHAR,
    _batch_id VARCHAR, _source_file VARCHAR,
    PRIMARY KEY (ticket_id, updated_at)
);

CREATE TABLE IF NOT EXISTS clean.tickets (
    ticket_id VARCHAR PRIMARY KEY, customer_id VARCHAR, created_at TIMESTAMP, updated_at TIMESTAMP,
    status VARCHAR, priority VARCHAR, product VARCHAR, agent_id VARCHAR, resolution_text VARCHAR,
    _batch_id VARCHAR, _source_file VARCHAR
);

CREATE TABLE IF NOT EXISTS clean.tickets_quarantine (
    _batch_id VARCHAR, _source_file VARCHAR, _row_number INTEGER,
    ticket_id VARCHAR, failed_checks VARCHAR, raw_record JSON, quarantined_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clean.customers (customer_id VARCHAR PRIMARY KEY, customer_name VARCHAR, region VARCHAR);
CREATE TABLE IF NOT EXISTS clean.agents (agent_id VARCHAR PRIMARY KEY, agent_name VARCHAR, team VARCHAR);

CREATE TABLE IF NOT EXISTS meta.ingest_log (
    batch_id VARCHAR PRIMARY KEY, file_name VARCHAR, file_hash VARCHAR, business_date DATE,
    rows_raw INTEGER, rows_valid INTEGER, rows_quarantined INTEGER, rows_upserted INTEGER,
    started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR
);

CREATE TABLE IF NOT EXISTS meta.dq_results (
    _batch_id VARCHAR, file_name VARCHAR, check_name VARCHAR, check_type VARCHAR, severity VARCHAR,
    total_rows INTEGER, failed_rows INTEGER, passed BOOLEAN, detail VARCHAR, run_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta.schema_changes (
    detected_at TIMESTAMP, file_name VARCHAR, table_name VARCHAR, column_name VARCHAR, change_type VARCHAR
);
"""

# Columns that clean tables carry besides the business columns.
CLEAN_META = ["_batch_id", "_source_file"]


def connect(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(DDL)
    return con


def table_columns(con, table: str) -> list[str]:
    schema, name = table.split(".")
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? ORDER BY ordinal_position",
        [schema, name],
    ).fetchall()
    return [r[0] for r in rows]


def apply_schema_drift(con, file_name: str, incoming_columns: list[str]) -> list[str]:
    """
    New columns in a file are added (as VARCHAR) to raw and clean tables and logged.
    Missing core columns are logged; the loader fills them with NULL.
    Returns the list of extra (non-core) columns in this file.
    """
    extras = [c for c in incoming_columns if c not in CORE_COLUMNS and not c.startswith("_")]
    for table in ("raw.tickets", "clean.ticket_versions", "clean.tickets"):
        existing = set(table_columns(con, table))
        for col in extras:
            if col not in existing:
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" VARCHAR')
                con.execute(
                    "INSERT INTO meta.schema_changes VALUES (now(), ?, ?, ?, 'added_column')",
                    [file_name, table, col],
                )
    for col in CORE_COLUMNS:
        if col not in incoming_columns:
            con.execute(
                "INSERT INTO meta.schema_changes VALUES (now(), ?, 'source_file', ?, 'missing_column')",
                [file_name, col],
            )
    return extras


def _aligned(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Return df with exactly `columns` (missing ones as NULL), in order."""
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = None
    return out[columns]


def insert_raw(con, df: pd.DataFrame) -> None:
    cols = table_columns(con, "raw.tickets")
    frame = _aligned(df, cols)
    con.register("_raw_batch", frame)
    col_list = ", ".join(f'"{c}"' for c in cols)
    con.execute(f"INSERT INTO raw.tickets ({col_list}) SELECT {col_list} FROM _raw_batch")
    con.unregister("_raw_batch")


def insert_quarantine(con, df: pd.DataFrame) -> None:
    if df.empty:
        return
    con.register("_q_batch", df)
    con.execute(
        """INSERT INTO clean.tickets_quarantine
           SELECT _batch_id, _source_file, _row_number, ticket_id, failed_checks, raw_record, now() FROM _q_batch"""
    )
    con.unregister("_q_batch")


def insert_versions(con, df: pd.DataFrame) -> None:
    """History of valid versions. Re-inserting an existing (ticket_id, updated_at) is a no-op."""
    if df.empty:
        return
    cols = table_columns(con, "clean.ticket_versions")
    con.register("_v_batch", _aligned(df, cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    con.execute(
        f"INSERT INTO clean.ticket_versions ({col_list}) SELECT {col_list} FROM _v_batch ON CONFLICT DO NOTHING"
    )
    con.unregister("_v_batch")


def upsert_current(con, df: pd.DataFrame) -> int:
    """
    Upsert into clean.tickets. A row only replaces the stored one if it is strictly newer,
    so a stale version arriving late (out of order) never overwrites fresher data,
    and re-running the same file changes nothing.
    Expects at most one row per ticket_id (see transform.latest_version_per_ticket).
    Returns number of rows inserted or updated.
    """
    if df.empty:
        return 0
    cols = table_columns(con, "clean.tickets")
    con.register("_u_batch", _aligned(df, cols))
    before = con.execute(
        "SELECT count(*) FROM _u_batch b LEFT JOIN clean.tickets t USING (ticket_id) "
        "WHERE t.ticket_id IS NULL OR b.updated_at > t.updated_at"
    ).fetchone()[0]
    col_list = ", ".join(f'"{c}"' for c in cols)
    set_list = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "ticket_id")
    con.execute(
        f"""INSERT INTO clean.tickets ({col_list}) SELECT {col_list} FROM _u_batch
            ON CONFLICT (ticket_id) DO UPDATE SET {set_list}
            WHERE EXCLUDED.updated_at > clean.tickets.updated_at"""
    )
    con.unregister("_u_batch")
    return int(before)


def rebuild_current_from_versions(con) -> None:
    """Recompute clean.tickets from history. Used after a forced reload of a file."""
    cols = table_columns(con, "clean.tickets")
    col_list = ", ".join(f'"{c}"' for c in cols)
    con.execute("DELETE FROM clean.tickets")
    con.execute(
        f"""INSERT INTO clean.tickets ({col_list})
            SELECT {col_list} FROM clean.ticket_versions
            QUALIFY row_number() OVER (PARTITION BY ticket_id ORDER BY updated_at DESC) = 1"""
    )


def load_reference(con, reference_dir: Path) -> None:
    """Reference data is small and authoritative, so it is fully replaced on each run."""
    for name, key in (("customers", "customer_id"), ("agents", "agent_id")):
        path = reference_dir / f"{name}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str)
        df[key] = df[key].str.strip().str.upper()
        con.execute(f"DELETE FROM clean.{name}")
        con.register("_ref", df)
        con.execute(f"INSERT INTO clean.{name} SELECT * FROM _ref")
        con.unregister("_ref")
