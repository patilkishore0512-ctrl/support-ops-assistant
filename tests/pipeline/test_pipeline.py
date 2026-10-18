"""End-to-end behaviour on small hand-written files: idempotency, upsert, out-of-order, drift, quarantine."""

import csv

import pytest

from pipeline import store
from pipeline.runner import run

HEADER = ["ticket_id", "customer_id", "created_at", "updated_at", "status",
          "priority", "product", "agent_id", "resolution_text"]


def write_csv(path, rows, header=HEADER):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def env(tmp_path):
    incoming, ref = tmp_path / "incoming", tmp_path / "reference"
    incoming.mkdir()
    ref.mkdir()
    write_csv(ref / "customers.csv", [["CUST-0001", "A", "APAC"]], ["customer_id", "customer_name", "region"])
    write_csv(ref / "agents.csv", [["AGT-001", "Agent", "Tier1"]], ["agent_id", "agent_name", "team"])
    con = store.connect(":memory:")
    return con, incoming, ref


def day1(incoming):
    write_csv(incoming / "tickets_2026-10-01.csv", [
        ["TKT-000001", "CUST-0001", "2026-09-30 09:00:00", "2026-09-30 10:00:00", "open", "high", "Billing", "", ""],
        ["TKT-000002", "CUST-0001", "2026-09-30 09:00:00", "2026-10-01 10:00:00", "Resolved", "LOW", "api", "AGT-001", "done"],
        ["TKT-000002", "CUST-0001", "2026-09-30 09:00:00", "2026-10-01 10:00:00", "Resolved", "LOW", "api", "AGT-001", "done"],
        ["TKT-000003", "CUST-0001", "2026-09-30 09:00:00", "2026-10-01 10:00:00", "WIP", "low", "api", "AGT-001", ""],
    ])


def count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_bad_rows_are_quarantined_not_dropped(env):
    con, incoming, ref = env
    day1(incoming)
    run(con, incoming, ref)
    assert count(con, "raw.tickets") == 4
    assert count(con, "clean.tickets") == 2
    assert count(con, "clean.tickets_quarantine") == 2  # duplicate + WIP status
    reasons = {r[0] for r in con.execute("SELECT failed_checks FROM clean.tickets_quarantine").fetchall()}
    assert reasons == {"exact_duplicate_row", "status_allowed_values"}


def test_rerun_same_day_is_idempotent(env):
    con, incoming, ref = env
    day1(incoming)
    run(con, incoming, ref)
    snapshot = [count(con, t) for t in ("raw.tickets", "clean.tickets", "clean.tickets_quarantine")]
    results = run(con, incoming, ref)
    assert results[0].status == "skipped"
    run(con, incoming, ref, force=True)
    assert [count(con, t) for t in ("raw.tickets", "clean.tickets", "clean.tickets_quarantine")] == snapshot


def test_later_version_updates_and_stale_version_is_ignored(env):
    con, incoming, ref = env
    day1(incoming)
    write_csv(incoming / "tickets_2026-10-02.csv", [
        ["TKT-000001", "CUST-0001", "2026-09-30 09:00:00", "2026-10-02 08:00:00", "resolved", "high", "billing", "AGT-001", "fixed"],
    ])
    write_csv(incoming / "tickets_2026-10-03.csv", [
        # stale copy of the day-1 version arrives late
        ["TKT-000001", "CUST-0001", "2026-09-30 09:00:00", "2026-09-30 10:00:00", "open", "high", "billing", "", ""],
    ])
    run(con, incoming, ref)
    status, = con.execute("SELECT status FROM clean.tickets WHERE ticket_id='TKT-000001'").fetchone()
    assert status == "resolved"
    assert count(con, "clean.tickets") == 2
    assert con.execute("SELECT count(*) FROM clean.ticket_versions WHERE ticket_id='TKT-000001'").fetchone()[0] == 2


def test_schema_drift_new_column_is_added(env):
    con, incoming, ref = env
    day1(incoming)
    write_csv(incoming / "tickets_2026-10-03.csv",
              [["TKT-000009", "CUST-0001", "2026-10-03 08:00:00", "2026-10-03 09:00:00", "Open", "Low", "API", "", "", "chat"]],
              header=[h.title() for h in HEADER] + ["Channel"])
    results = run(con, incoming, ref)
    assert all(r.status == "loaded" for r in results)
    assert con.execute("SELECT channel FROM clean.tickets WHERE ticket_id='TKT-000009'").fetchone()[0] == "chat"
    assert con.execute("SELECT count(*) FROM meta.schema_changes WHERE column_name='channel'").fetchone()[0] == 3


def test_curated_views(env):
    con, incoming, ref = env
    day1(incoming)
    run(con, incoming, ref)
    hours = con.execute("SELECT avg_resolution_hours FROM curated.avg_resolution_by_product WHERE product='api'").fetchone()[0]
    assert hours == pytest.approx(25.0)  # 2026-09-30 09:00 -> 2026-10-01 10:00
    backlog = con.execute(
        "SELECT open_backlog_at_week_end FROM curated.backlog_by_priority_week WHERE priority='high'"
    ).fetchall()
    assert backlog == [(1,)]  # TKT-000001 still open
