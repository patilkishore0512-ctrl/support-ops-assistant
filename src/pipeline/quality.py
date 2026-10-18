"""
Data quality checks.

Two kinds:
  - row checks return a boolean mask of FAILING rows.
      severity "error" -> row is quarantined (kept in a quarantine table with reasons)
      severity "warn"  -> row is kept, failure is only logged
  - dataset checks look at the whole batch (freshness, volume) and are logged.

Every check result (pass or fail) is written to meta.dq_results, so nothing is
dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd

from pipeline.config import (
    ALLOWED_PRIORITY,
    ALLOWED_PRODUCT,
    ALLOWED_STATUS,
    CORE_COLUMNS,
    FRESHNESS_MAX_LAG_DAYS,
    FUTURE_TOLERANCE_DAYS,
    MIN_ROWS_PER_FILE,
    RESOLVED_STATUS,
    TICKET_ID_PATTERN,
)
from pipeline.transform import row_fingerprint


@dataclass
class CheckContext:
    run_date: date
    valid_customers: set[str] = field(default_factory=set)
    valid_agents: set[str] = field(default_factory=set)


@dataclass
class RowCheck:
    name: str
    severity: str  # "error" | "warn"
    description: str
    fn: Callable[[pd.DataFrame, CheckContext], pd.Series]


@dataclass
class CheckResult:
    check_name: str
    check_type: str  # "row" | "dataset"
    severity: str
    total_rows: int
    failed_rows: int
    passed: bool
    detail: str = ""


def _future_cutoff(ctx: CheckContext) -> datetime:
    return datetime.combine(ctx.run_date, datetime.min.time()) + timedelta(days=1 + FUTURE_TOLERANCE_DAYS)


ROW_CHECKS: list[RowCheck] = [
    RowCheck("ticket_id_not_null", "error", "ticket_id must be present",
             lambda df, c: df["ticket_id"].isna()),
    RowCheck("ticket_id_format", "error", f"ticket_id must match {TICKET_ID_PATTERN}",
             lambda df, c: df["ticket_id"].notna()
             & ~df["ticket_id"].astype("string").str.match(TICKET_ID_PATTERN).fillna(False)),
    RowCheck("exact_duplicate_row", "error", "row is an exact copy of an earlier row in the same file",
             lambda df, c: row_fingerprint(df, CORE_COLUMNS).duplicated(keep="first").set_axis(df.index)),
    RowCheck("customer_id_not_null", "error", "customer_id must be present",
             lambda df, c: df["customer_id"].isna()),
    RowCheck("status_allowed_values", "error", f"status in {sorted(ALLOWED_STATUS)}",
             lambda df, c: ~df["status"].isin(ALLOWED_STATUS)),
    RowCheck("priority_allowed_values", "error", f"priority in {sorted(ALLOWED_PRIORITY)}",
             lambda df, c: ~df["priority"].isin(ALLOWED_PRIORITY)),
    RowCheck("product_allowed_values", "error", f"product in {sorted(ALLOWED_PRODUCT)}",
             lambda df, c: ~df["product"].isin(ALLOWED_PRODUCT)),
    RowCheck("created_at_valid_date", "error", "created_at must parse as a date",
             lambda df, c: df["created_at"].isna()),
    RowCheck("updated_at_valid_date", "error", "updated_at must parse as a date",
             lambda df, c: df["updated_at"].isna()),
    RowCheck("updated_not_before_created", "error", "updated_at >= created_at",
             lambda df, c: (df["updated_at"] < df["created_at"]).fillna(False)),
    RowCheck("dates_not_in_future", "error",
             f"dates no later than file date + {FUTURE_TOLERANCE_DAYS} day",
             lambda df, c: ((df["created_at"] > _future_cutoff(c)) | (df["updated_at"] > _future_cutoff(c))).fillna(False)),
    RowCheck("customer_id_referential", "error", "customer_id exists in reference customers",
             lambda df, c: df["customer_id"].notna() & ~df["customer_id"].isin(c.valid_customers)),
    RowCheck("agent_id_referential", "error", "agent_id (when present) exists in reference agents",
             lambda df, c: df["agent_id"].notna() & ~df["agent_id"].isin(c.valid_agents)),
    RowCheck("resolution_text_when_resolved", "warn", "resolved/closed tickets should have resolution_text",
             lambda df, c: df["status"].isin(RESOLVED_STATUS) & df["resolution_text"].isna()),
]


def run_row_checks(df: pd.DataFrame, ctx: CheckContext) -> tuple[pd.Series, list[CheckResult]]:
    """
    Returns:
      reasons: per-row list of failed ERROR check names (empty list = row is good)
      results: one CheckResult per check
    """
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype="object")
    results: list[CheckResult] = []
    for check in ROW_CHECKS:
        failed = check.fn(df, ctx).astype(bool)
        n_failed = int(failed.sum())
        results.append(CheckResult(check.name, "row", check.severity, len(df), n_failed, n_failed == 0, check.description))
        if check.severity == "error" and n_failed:
            for idx in failed[failed].index:
                reasons.at[idx] = reasons.at[idx] + [check.name]
    return reasons, results


def run_dataset_checks(df: pd.DataFrame, ctx: CheckContext) -> list[CheckResult]:
    results = []

    n = len(df)
    results.append(CheckResult("min_row_count", "dataset", "warn", n, 0 if n >= MIN_ROWS_PER_FILE else n,
                               n >= MIN_ROWS_PER_FILE, f"rows={n}, minimum={MIN_ROWS_PER_FILE}"))

    newest = df["updated_at"].max() if n else pd.NaT
    run_start = datetime.combine(ctx.run_date, datetime.min.time())
    fresh = pd.notna(newest) and newest >= run_start - timedelta(days=FRESHNESS_MAX_LAG_DAYS)
    results.append(CheckResult("freshness", "dataset", "warn", n, 0 if fresh else n, bool(fresh),
                               f"max updated_at={newest}, file date={ctx.run_date}, max lag={FRESHNESS_MAX_LAG_DAYS}d"))
    return results
