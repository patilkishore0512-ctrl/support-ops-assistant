"""
Pure transformation functions (no database access) so they are easy to unit test.

raw (all strings) --standardize()--> typed, normalized frame
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from pipeline.config import ALLOWED_PRODUCT, CORE_COLUMNS, NULL_TOKENS

# Explicit formats, tried in order. Explicit beats pandas' inference because
# inference can silently swap day and month.
DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%d-%b-%Y %H:%M",
]

PRODUCT_ALIASES = {
    "billing": "billing",
    "mobile app": "mobile_app",
    "web portal": "web_portal",
    "api": "api",
    "integrations": "integrations",
}


def normalize_column_name(name: str) -> str:
    """'Ticket_Id' / ' Ticket ID ' / 'ticket-id' -> 'ticket_id'."""
    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: normalize_column_name(c) for c in df.columns})


def to_null(series: pd.Series) -> pd.Series:
    """Trim whitespace and turn null-like tokens into real nulls."""
    s = series.astype("object").where(series.notna(), None)
    s = s.map(lambda v: v.strip() if isinstance(v, str) else v)
    return s.map(lambda v: None if v is None or str(v).lower() in NULL_TOKENS else v)


def normalize_id(series: pd.Series) -> pd.Series:
    """Trim and upper-case identifiers: ' tkt-000001 ' -> 'TKT-000001'."""
    return to_null(series).map(lambda v: v.upper() if isinstance(v, str) else v)


def normalize_category(series: pd.Series) -> pd.Series:
    """'In Progress' / 'in-progress' / 'IN_PROGRESS' -> 'in_progress'."""
    def _norm(v):
        if not isinstance(v, str):
            return v
        return re.sub(r"[\s\-]+", "_", v.strip().lower())
    return to_null(series).map(_norm)


def normalize_product(series: pd.Series) -> pd.Series:
    """Map product spellings to a canonical slug; unknown values pass through for the DQ check."""
    def _norm(v):
        if not isinstance(v, str):
            return v
        key = re.sub(r"[\s_\-]+", " ", v.strip().lower())
        if key in PRODUCT_ALIASES:
            return PRODUCT_ALIASES[key]
        slug = key.replace(" ", "_")
        return slug if slug in ALLOWED_PRODUCT else v.strip()
    return to_null(series).map(_norm)


def parse_datetime(series: pd.Series) -> pd.Series:
    """
    Parse mixed-format date strings into naive UTC timestamps.
    Unparseable values become NaT (the DQ layer then quarantines them).
    """
    s = to_null(series)
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    as_str = s.astype("object")
    is_epoch = as_str.map(lambda v: isinstance(v, str) and v.isdigit() and len(v) in (10, 13))
    if is_epoch.any():
        epoch = pd.to_numeric(as_str[is_epoch])
        unit_s = epoch < 10**11
        parsed = pd.Series(pd.NaT, index=epoch.index, dtype="datetime64[ns]")
        if unit_s.any():
            parsed[unit_s] = pd.to_datetime(epoch[unit_s], unit="s")
        if (~unit_s).any():
            parsed[~unit_s] = pd.to_datetime(epoch[~unit_s], unit="ms")
        result[is_epoch] = parsed

    remaining = result.isna() & s.notna() & ~is_epoch
    for fmt in DATE_FORMATS:
        if not remaining.any():
            break
        attempt = pd.to_datetime(as_str[remaining], format=fmt, errors="coerce")
        result[remaining] = attempt
        remaining = result.isna() & s.notna() & ~is_epoch
    return result.astype("datetime64[ns]")


def standardize(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Turn a raw string frame into a typed, normalized frame.
    Extra (drifted) columns are kept as trimmed strings.
    Keeps _raw_* copies of the date strings so quarantine rows show the original value.
    """
    df = normalize_columns(raw.copy())
    for col in CORE_COLUMNS:
        if col not in df.columns:
            df[col] = None

    out = pd.DataFrame(index=df.index)
    out["ticket_id"] = normalize_id(df["ticket_id"])
    out["customer_id"] = normalize_id(df["customer_id"])
    out["agent_id"] = normalize_id(df["agent_id"])
    out["created_at"] = parse_datetime(df["created_at"])
    out["updated_at"] = parse_datetime(df["updated_at"])
    out["_raw_created_at"] = to_null(df["created_at"])
    out["_raw_updated_at"] = to_null(df["updated_at"])
    out["status"] = normalize_category(df["status"])
    out["priority"] = normalize_category(df["priority"])
    out["product"] = normalize_product(df["product"])
    out["resolution_text"] = to_null(df["resolution_text"])

    for col in df.columns:
        if col not in CORE_COLUMNS and col not in out.columns:
            out[col] = to_null(df[col])
    return out


def row_fingerprint(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Stable hash of a row's values, used to detect exact duplicates."""
    return pd.util.hash_pandas_object(df[columns].astype("string").fillna("<NULL>"), index=False)


def latest_version_per_ticket(df: pd.DataFrame) -> pd.DataFrame:
    """Within one batch, keep only the newest version of each ticket (ties: last row wins)."""
    if df.empty:
        return df
    ordered = df.assign(_ord=np.arange(len(df))).sort_values(["ticket_id", "updated_at", "_ord"])
    return ordered.drop_duplicates("ticket_id", keep="last").drop(columns="_ord")
