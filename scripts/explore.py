"""
Run a read-only SQL query against the ticket store and print the result.

    python scripts/explore.py "SELECT * FROM clean.tickets LIMIT 5"
    python scripts/explore.py tables          # list every table and view with row counts
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

DB = Path(__file__).resolve().parents[1] / "warehouse" / "tickets.duckdb"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    if not DB.exists():
        print(f"{DB} not found: run `python -m pipeline run` first")
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    query = sys.argv[1]
    if query.strip().lower() == "tables":
        names = con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('raw', 'clean', 'curated', 'meta') ORDER BY 1"
        ).fetchall()
        query = " UNION ALL ".join(f"SELECT '{n[0]}' AS name, count(*) AS rows FROM {n[0]}" for n in names)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 60)
    df = con.execute(query).df()
    print(df.to_string(index=False) if not df.empty else "(no rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
