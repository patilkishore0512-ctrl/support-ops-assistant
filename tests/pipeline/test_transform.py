import pandas as pd
import pytest

from pipeline.transform import (
    latest_version_per_ticket,
    normalize_category,
    normalize_column_name,
    normalize_id,
    normalize_product,
    parse_datetime,
    standardize,
    to_null,
)


@pytest.mark.parametrize("raw,expected", [
    ("Ticket_Id", "ticket_id"),
    (" Ticket ID ", "ticket_id"),
    ("ticket-id", "ticket_id"),
    ("CHANNEL", "channel"),
])
def test_normalize_column_name(raw, expected):
    assert normalize_column_name(raw) == expected


def test_to_null_handles_tokens_and_whitespace():
    out = to_null(pd.Series(["", " ", "NULL", "n/a", "None", " value ", None]))
    assert out.isna().tolist() == [True, True, True, True, True, False, True]
    assert out.iloc[5] == "value"


def test_normalize_id_trims_and_uppercases():
    out = normalize_id(pd.Series([" tkt-000001 ", "TKT-000002", ""]))
    assert out.iloc[:2].tolist() == ["TKT-000001", "TKT-000002"]
    assert pd.isna(out.iloc[2])


@pytest.mark.parametrize("raw,expected", [
    ("In Progress", "in_progress"),
    ("in-progress", "in_progress"),
    ("IN_PROGRESS", "in_progress"),
    ("resolved ", "resolved"),
    ("WIP", "wip"),  # normalized but still invalid -> caught by DQ check
])
def test_normalize_category(raw, expected):
    assert normalize_category(pd.Series([raw])).iloc[0] == expected


@pytest.mark.parametrize("raw,expected", [
    ("Mobile App", "mobile_app"),
    ("MOBILE_APP", "mobile_app"),
    ("mobile-app", "mobile_app"),
    (" Billing ", "billing"),
    (" Api", "api"),
    ("Legacy CRM", "Legacy CRM"),  # unknown value passes through for quarantine
])
def test_normalize_product(raw, expected):
    assert normalize_product(pd.Series([raw])).iloc[0] == expected


def test_parse_datetime_all_supported_formats():
    expected = pd.Timestamp("2026-09-27 12:03:00")
    values = [
        "2026-09-27 12:03:00",
        "2026-09-27T12:03:00Z",
        "2026-09-27T12:03:00",
        "2026/09/27 12:03",
        "27-Sep-2026 12:03",
        str(int(expected.timestamp())),           # epoch seconds
        str(int(expected.timestamp()) * 1000),    # epoch millis
    ]
    parsed = parse_datetime(pd.Series(values))
    assert (parsed == expected).all(), parsed.tolist()


def test_parse_datetime_invalid_becomes_nat():
    parsed = parse_datetime(pd.Series(["2026-13-45 99:00:00", "not a date", "", None]))
    assert parsed.isna().all()


def test_standardize_keeps_drifted_columns_and_fills_missing():
    raw = pd.DataFrame({
        "Ticket_Id": [" tkt-000001"],
        "Status": ["Resolved"],
        "Priority": ["HIGH"],
        "Product": ["web portal"],
        "Created_At": ["2026-09-01 10:00:00"],
        "Updated_At": ["2026/09/02 10:00"],
        "Channel": ["chat"],
        # customer_id, agent_id, resolution_text missing on purpose
    })
    out = standardize(raw)
    row = out.iloc[0]
    assert row["ticket_id"] == "TKT-000001"
    assert row["status"] == "resolved"
    assert row["priority"] == "high"
    assert row["product"] == "web_portal"
    assert row["channel"] == "chat"
    assert pd.isna(row["customer_id"])
    assert row["updated_at"] == pd.Timestamp("2026-09-02 10:00")


def test_latest_version_per_ticket_keeps_newest():
    df = pd.DataFrame({
        "ticket_id": ["A", "A", "B", "A"],
        "updated_at": pd.to_datetime(["2026-10-01", "2026-10-03", "2026-10-01", "2026-10-02"]),
        "status": ["open", "resolved", "open", "pending"],
    })
    out = latest_version_per_ticket(df).set_index("ticket_id")
    assert out.loc["A", "status"] == "resolved"
    assert len(out) == 2
