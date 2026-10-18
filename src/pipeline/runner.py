"""
Processes one daily file end to end, inside a single transaction:

  read -> drift check -> raw insert -> standardize -> DQ checks
       -> quarantine bad rows -> versions insert -> upsert current -> log

Idempotency
  - Each file is identified by name + SHA-256 of its content.
  - Same name, same hash already loaded  -> skipped.
  - Same name, different hash (re-export) or --force -> the file's previous rows are
    removed from raw / versions / quarantine / dq results and it is reprocessed,
    then clean.tickets is rebuilt from history.
  - Everything happens in one transaction, so a crash leaves no partial batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from pipeline import store
from pipeline.config import CORE_COLUMNS
from pipeline.curated import create_curated_views
from pipeline.quality import CheckContext, run_dataset_checks, run_row_checks
from pipeline.transform import latest_version_per_ticket, normalize_columns, standardize

log = logging.getLogger("pipeline")

FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class BatchResult:
    file_name: str
    status: str  # loaded | skipped | failed
    rows_raw: int = 0
    rows_valid: int = 0
    rows_quarantined: int = 0
    rows_upserted: int = 0
    message: str = ""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def business_date_from_name(path: Path) -> date:
    m = FILE_DATE_RE.search(path.name)
    if not m:
        raise ValueError(f"cannot find YYYY-MM-DD in file name: {path.name}")
    return datetime.strptime(m.group(1), "%Y-%m-%d").date()


def read_source(path: Path) -> pd.DataFrame:
    """Read CSV or JSON (array or JSON lines) with every value as a string."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    elif suffix in (".json", ".jsonl", ".ndjson"):
        lines = suffix != ".json" or path.read_text(encoding="utf-8").lstrip().startswith("{")
        df = pd.read_json(path, lines=lines, dtype=False).astype("string")
    else:
        raise ValueError(f"unsupported file type: {path}")
    return df.astype("object").where(df.notna(), None)


def _already_loaded(con, file_name: str, file_hash: str) -> str | None:
    """Return 'same' if identical content loaded, 'changed' if name loaded with other content, else None."""
    row = con.execute(
        "SELECT file_hash FROM meta.ingest_log WHERE file_name=? AND status='loaded' ORDER BY finished_at DESC LIMIT 1",
        [file_name],
    ).fetchone()
    if row is None:
        return None
    return "same" if row[0] == file_hash else "changed"


def _purge_file(con, file_name: str) -> None:
    for table, col in (
        ("raw.tickets", "_source_file"),
        ("clean.ticket_versions", "_source_file"),
        ("clean.tickets_quarantine", "_source_file"),
        ("meta.dq_results", "file_name"),
    ):
        con.execute(f"DELETE FROM {table} WHERE {col} = ?", [file_name])
    con.execute("UPDATE meta.ingest_log SET status='superseded' WHERE file_name=? AND status='loaded'", [file_name])


def process_file(con, path: Path, force: bool = False) -> BatchResult:
    file_name = path.name
    file_hash = file_sha256(path)
    state = _already_loaded(con, file_name, file_hash)
    if state == "same" and not force:
        log.info("skip %s: already loaded with identical content", file_name)
        return BatchResult(file_name, "skipped", message="already loaded")

    batch_id = uuid.uuid4().hex[:12]
    run_date = business_date_from_name(path)
    started = datetime.now()

    con.execute("BEGIN TRANSACTION")
    try:
        reprocess = state is not None  # changed content, or forced
        if reprocess:
            log.info("reprocessing %s (%s)", file_name, "forced" if state == "same" else "content changed")
            _purge_file(con, file_name)

        # 1. read + schema drift
        source = normalize_columns(read_source(path))
        extras = store.apply_schema_drift(con, file_name, list(source.columns))
        if extras:
            log.info("schema drift in %s: extra columns %s", file_name, extras)

        # 2. raw layer: values exactly as received, plus load metadata
        raw = source.copy()
        raw["_batch_id"] = batch_id
        raw["_source_file"] = file_name
        raw["_row_number"] = range(1, len(raw) + 1)
        raw["_loaded_at"] = started
        store.insert_raw(con, raw)

        # 3. standardize + quality checks
        std = standardize(source)
        ctx = CheckContext(
            run_date=run_date,
            valid_customers={r[0] for r in con.execute("SELECT customer_id FROM clean.customers").fetchall()},
            valid_agents={r[0] for r in con.execute("SELECT agent_id FROM clean.agents").fetchall()},
        )
        reasons, row_results = run_row_checks(std, ctx)
        bad = reasons.map(len) > 0

        # 4. quarantine (keep the original record and every failed check)
        quarantine = pd.DataFrame({
            "_batch_id": batch_id,
            "_source_file": file_name,
            "_row_number": raw.loc[bad, "_row_number"].astype(int),
            "ticket_id": std.loc[bad, "ticket_id"],
            "failed_checks": reasons[bad].map(lambda r: ",".join(r)),
            "raw_record": source.loc[bad].apply(lambda r: json.dumps(r.to_dict(), default=str), axis=1)
            if bad.any() else pd.Series(dtype="object"),
        })
        store.insert_quarantine(con, quarantine)

        # 5. valid rows -> history + current
        good = std.loc[~bad].drop(columns=["_raw_created_at", "_raw_updated_at"])
        good["_batch_id"] = batch_id
        good["_source_file"] = file_name
        store.insert_versions(con, good)
        if reprocess:
            store.rebuild_current_from_versions(con)
            upserted = len(latest_version_per_ticket(good))
        else:
            upserted = store.upsert_current(con, latest_version_per_ticket(good))

        # 6. dataset checks + log every result
        all_results = row_results + run_dataset_checks(std.loc[~bad], ctx)
        for r in all_results:
            con.execute(
                "INSERT INTO meta.dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())",
                [batch_id, file_name, r.check_name, r.check_type, r.severity,
                 r.total_rows, r.failed_rows, r.passed, r.detail],
            )
            if not r.passed:
                level = logging.WARNING if r.severity == "error" else logging.INFO
                log.log(level, "DQ %-30s %s failed=%d/%d", r.check_name, r.severity, r.failed_rows, r.total_rows)

        result = BatchResult(file_name, "loaded", len(raw), int((~bad).sum()), int(bad.sum()), upserted)
        con.execute(
            "INSERT INTO meta.ingest_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now(), 'loaded')",
            [batch_id, file_name, file_hash, run_date, result.rows_raw, result.rows_valid,
             result.rows_quarantined, result.rows_upserted, started],
        )
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        log.exception("failed to process %s", file_name)
        con.execute(
            "INSERT INTO meta.ingest_log VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, now(), 'failed')",
            [batch_id, file_name, file_hash, run_date, started],
        )
        return BatchResult(file_name, "failed", message=str(exc))

    create_curated_views(con)
    log.info("loaded %s: raw=%d valid=%d quarantined=%d upserted=%d",
             file_name, result.rows_raw, result.rows_valid, result.rows_quarantined, result.rows_upserted)
    return result


def discover_files(incoming_dir: Path, day: str | None = None) -> list[Path]:
    files = sorted(
        p for p in incoming_dir.glob("tickets_*")
        if p.suffix.lower() in (".csv", ".json", ".jsonl", ".ndjson")
    )
    if day:
        files = [p for p in files if day in p.name]
    # business date order matters: older files first so upserts apply in sequence
    return sorted(files, key=business_date_from_name)


def run(con, incoming_dir: Path, reference_dir: Path, day: str | None = None, force: bool = False) -> list[BatchResult]:
    store.load_reference(con, reference_dir)
    files = discover_files(incoming_dir, day)
    if not files:
        log.warning("no files found in %s%s", incoming_dir, f" for day {day}" if day else "")
    results = [process_file(con, f, force=force) for f in files]
    create_curated_views(con)
    return results


__all__ = ["run", "process_file", "discover_files", "BatchResult", "CORE_COLUMNS"]
