from datetime import date

import pandas as pd

from pipeline.quality import CheckContext, run_dataset_checks, run_row_checks
from pipeline.transform import standardize

CTX = CheckContext(
    run_date=date(2026, 10, 2),
    valid_customers={"CUST-0001"},
    valid_agents={"AGT-001"},
)


def good_row(**overrides):
    row = {
        "ticket_id": "TKT-000001", "customer_id": "CUST-0001",
        "created_at": "2026-09-30 10:00:00", "updated_at": "2026-10-01 10:00:00",
        "status": "resolved", "priority": "high", "product": "Billing",
        "agent_id": "AGT-001", "resolution_text": "Fixed.",
    }
    row.update(overrides)
    return row


def failures_for(**overrides):
    df = standardize(pd.DataFrame([good_row(**overrides)]))
    reasons, _ = run_row_checks(df, CTX)
    return set(reasons.iloc[0])


def test_good_row_passes_everything():
    assert failures_for() == set()


def test_each_rule_is_caught():
    cases = {
        "ticket_id_not_null": {"ticket_id": ""},
        "ticket_id_format": {"ticket_id": "TICKET#1"},
        "customer_id_not_null": {"customer_id": None},
        "status_allowed_values": {"status": "WIP"},
        "priority_allowed_values": {"priority": "P0"},
        "product_allowed_values": {"product": "Legacy CRM"},
        "created_at_valid_date": {"created_at": "2026-13-45 99:00:00"},
        "updated_at_valid_date": {"updated_at": "garbage"},
        "updated_not_before_created": {"updated_at": "2026-09-01 00:00:00"},
        "dates_not_in_future": {"updated_at": "2026-12-01 00:00:00"},
        "customer_id_referential": {"customer_id": "CUST-9999"},
        "agent_id_referential": {"agent_id": "AGT-999"},
    }
    for check, overrides in cases.items():
        assert check in failures_for(**overrides), check


def test_null_agent_is_allowed():
    assert failures_for(agent_id="", status="open", resolution_text="") == set()


def test_exact_duplicate_only_flags_second_copy():
    df = standardize(pd.DataFrame([good_row(), good_row(), good_row(ticket_id="TKT-000002")]))
    reasons, _ = run_row_checks(df, CTX)
    assert [("exact_duplicate_row" in r) for r in reasons] == [False, True, False]


def test_warn_check_does_not_quarantine():
    df = standardize(pd.DataFrame([good_row(resolution_text="")]))
    reasons, results = run_row_checks(df, CTX)
    assert reasons.iloc[0] == []
    warn = next(r for r in results if r.check_name == "resolution_text_when_resolved")
    assert warn.failed_rows == 1 and not warn.passed


def test_freshness_check():
    fresh = standardize(pd.DataFrame([good_row()]))
    stale = standardize(pd.DataFrame([good_row(updated_at="2026-09-01 00:00:00", created_at="2026-08-01 00:00:00")]))
    assert next(r for r in run_dataset_checks(fresh, CTX) if r.check_name == "freshness").passed
    assert not next(r for r in run_dataset_checks(stale, CTX) if r.check_name == "freshness").passed
