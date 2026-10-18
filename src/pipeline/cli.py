"""
Orchestration entry point.

    python -m pipeline generate                 # create synthetic data
    python -m pipeline run                      # load every file in data/incoming (skips loaded ones)
    python -m pipeline run --day 2026-10-02     # load one day
    python -m pipeline run --day 2026-10-02 --force   # reprocess a day
    python -m pipeline report                   # print curated outputs + DQ summary, write outputs/
    python -m pipeline status                   # ingest log
    python -m pipeline reset                    # delete the warehouse file
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pipeline import config, store
from pipeline.curated import create_curated_views
from pipeline.runner import run


def _print(con, title: str, sql: str) -> None:
    df = con.execute(sql).df()
    print(f"\n=== {title} ({len(df)} rows) ===")
    print(df.to_string(index=False) if not df.empty else "(empty)")


def cmd_generate(args) -> int:
    import runpy
    script = config.ROOT / "scripts" / "generate_tickets.py"
    gen = runpy.run_path(str(script))["main"]
    gen(str(config.ROOT / "data"), args.seed)
    return 0


def cmd_run(args) -> int:
    con = store.connect(args.db)
    results = run(con, Path(args.incoming), Path(args.reference), day=args.day, force=args.force)
    print(f"\n{'file':<28}{'status':<10}{'raw':>7}{'valid':>7}{'quar.':>7}{'upsert':>8}")
    for r in results:
        print(f"{r.file_name:<28}{r.status:<10}{r.rows_raw:>7}{r.rows_valid:>7}{r.rows_quarantined:>7}{r.rows_upserted:>8}  {r.message}")
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_report(args) -> int:
    con = store.connect(args.db)
    create_curated_views(con)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    queries = {
        "table_counts": """
            SELECT 'raw.tickets' AS table_name, count(*) AS rows FROM raw.tickets
            UNION ALL SELECT 'clean.ticket_versions', count(*) FROM clean.ticket_versions
            UNION ALL SELECT 'clean.tickets', count(*) FROM clean.tickets
            UNION ALL SELECT 'clean.tickets_quarantine', count(*) FROM clean.tickets_quarantine""",
        "avg_resolution_by_product": "SELECT * FROM curated.avg_resolution_by_product",
        "backlog_by_priority_week": "SELECT * FROM curated.backlog_by_priority_week",
        "dq_summary": "SELECT * FROM curated.dq_summary",
        "quarantine_reasons": """
            SELECT reason, count(*) AS rows FROM (
                SELECT unnest(string_split(failed_checks, ',')) AS reason FROM clean.tickets_quarantine)
            GROUP BY reason ORDER BY rows DESC""",
        "schema_changes": "SELECT * FROM meta.schema_changes ORDER BY detected_at",
        "ingest_log": "SELECT file_name, business_date, rows_raw, rows_valid, rows_quarantined, rows_upserted, status FROM meta.ingest_log ORDER BY started_at",
    }
    for name, sql in queries.items():
        _print(con, name, sql)
        con.execute(sql).df().to_csv(out / f"{name}.csv", index=False)
    print(f"\nwrote CSVs to {out}/")
    return 0


def cmd_status(args) -> int:
    con = store.connect(args.db)
    _print(con, "ingest_log", "SELECT * FROM meta.ingest_log ORDER BY started_at")
    return 0


def cmd_reset(args) -> int:
    db = Path(args.db)
    for p in (db, db.with_suffix(db.suffix + ".wal")):
        if p.exists():
            p.unlink()
            print(f"deleted {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline", description="Support ticket pipeline")
    p.add_argument("--db", default=str(config.DB_PATH), help="DuckDB file path")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate synthetic ticket files")
    g.add_argument("--seed", type=int, default=42)
    g.set_defaults(fn=cmd_generate)

    r = sub.add_parser("run", help="ingest daily files")
    r.add_argument("--day", help="YYYY-MM-DD; default: all files")
    r.add_argument("--force", action="store_true", help="reprocess even if already loaded")
    r.add_argument("--incoming", default=str(config.INCOMING_DIR))
    r.add_argument("--reference", default=str(config.REFERENCE_DIR))
    r.set_defaults(fn=cmd_run)

    rep = sub.add_parser("report", help="print curated outputs and write them to CSV")
    rep.add_argument("--out", default=str(config.OUTPUT_DIR))
    rep.set_defaults(fn=cmd_report)

    sub.add_parser("status", help="show ingest log").set_defaults(fn=cmd_status)
    sub.add_parser("reset", help="delete the warehouse").set_defaults(fn=cmd_reset)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
