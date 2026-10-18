"""Central configuration. Paths can be overridden with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# src/pipeline/config.py -> project root is two levels up from this package
ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))

DB_PATH = Path(os.getenv("TICKETS_DB_PATH", ROOT / "warehouse" / "tickets.duckdb"))
INCOMING_DIR = Path(os.getenv("TICKETS_INCOMING_DIR", ROOT / "data" / "incoming"))
REFERENCE_DIR = Path(os.getenv("TICKETS_REFERENCE_DIR", ROOT / "data" / "reference"))
OUTPUT_DIR = Path(os.getenv("TICKETS_OUTPUT_DIR", ROOT / "outputs" / "pipeline"))

# The contract: columns we expect in every file. Anything else is schema drift.
CORE_COLUMNS = [
    "ticket_id",
    "customer_id",
    "created_at",
    "updated_at",
    "status",
    "priority",
    "product",
    "agent_id",
    "resolution_text",
]

ALLOWED_STATUS = {"open", "pending", "in_progress", "resolved", "closed"}
RESOLVED_STATUS = {"resolved", "closed"}
ALLOWED_PRIORITY = {"low", "medium", "high", "urgent"}
ALLOWED_PRODUCT = {"billing", "mobile_app", "web_portal", "api", "integrations"}

TICKET_ID_PATTERN = r"^TKT-\d{6}$"

# Strings that mean "no value" in source files.
NULL_TOKENS = {"", "null", "none", "nan", "n/a", "na", "-"}

# Freshness: the newest updated_at in a daily file should be within this many
# days of the file's business date.
FRESHNESS_MAX_LAG_DAYS = 1
# Dates more than this far after the file date are treated as invalid.
FUTURE_TOLERANCE_DAYS = 1
# A daily file smaller than this is suspicious (partial export).
MIN_ROWS_PER_FILE = 100
