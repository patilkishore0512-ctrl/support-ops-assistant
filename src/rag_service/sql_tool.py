"""
Read-only, guarded SQL over the Part A ticket store.

Flow:  question -> SQL writer (rules for the mock, LLM otherwise) -> guard -> execute -> rows

Guard layers (any failure = the query does not run):
  1. Parse with sqlglot (DuckDB dialect). Exactly one statement.
  2. The statement must be a SELECT (CTEs and UNIONs of SELECTs allowed).
  3. No write/DDL/admin node anywhere in the tree (INSERT, UPDATE, DELETE, CREATE, DROP,
     ALTER, COPY, ATTACH, PRAGMA, SET, ...), including inside subqueries.
  4. Only allow-listed tables. raw.*, meta.*, the quarantine table (holds raw records with
     PII) and customer names are not queryable.
  5. No file / network / system functions (read_csv, read_parquet, glob, getenv, ...).
  6. A LIMIT is forced (at most SQL_ROW_LIMIT rows).
Execution layers:
  7. The DuckDB connection is opened read_only with external access disabled and the
     configuration locked, so even a query that slipped past the parser cannot write,
     read files or reach the network.
  8. A timeout interrupts long-running queries.
  9. PII in returned values is masked.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from rag_service.guardrails import mask_value

ALLOWED_TABLES = {
    "clean.tickets",
    "clean.agents",
    "curated.avg_resolution_by_product",
    "curated.backlog_by_priority_week",
    "curated.ticket_resolution",
}

SCHEMA_DOC = """\
clean.tickets(ticket_id, customer_id, created_at TIMESTAMP, updated_at TIMESTAMP, status, priority, product, agent_id, resolution_text, channel)
  -- current state, one row per ticket
  -- status in (open, pending, in_progress, resolved, closed); priority in (low, medium, high, urgent)
  -- product in (billing, mobile_app, web_portal, api, integrations)
clean.agents(agent_id, agent_name, team)
curated.avg_resolution_by_product(product, resolved_tickets, avg_resolution_hours, median_resolution_hours, p90_resolution_hours)
curated.backlog_by_priority_week(week_start DATE, priority, open_backlog_at_week_end, created_in_week, resolved_in_week)
curated.ticket_resolution(ticket_id, product, priority, status, created_at, resolved_at, resolution_hours)"""

FORBIDDEN_NODES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
    "Command", "Copy", "Attach", "Detach", "Pragma", "Set", "Use", "Grant", "Transaction", "Commit",
    "Rollback", "Install", "Load", "Export",
)
FORBIDDEN_FUNC_RE = re.compile(
    r"^(read_|scan_|glob$|getenv$|current_setting$|duckdb_|pragma_|sniff_|query$|query_table$|"
    r"parquet_|delta_scan|iceberg_|sqlite_|postgres_|mysql_|httpfs|load_|install_)",
    re.I,
)

TABLE_FUNCTION_CLASSES = tuple(
    getattr(exp, n) for n in ("ReadCSV", "ReadParquet", "ReadJSON") if hasattr(exp, n)
)


class SQLGuardError(ValueError):
    pass


def guard_sql(sql: str, row_limit: int = 100) -> str:
    """Validate a query and return the safe SQL that will actually run (with LIMIT)."""
    if not sql or not sql.strip():
        raise SQLGuardError("empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLGuardError(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise SQLGuardError("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise SQLGuardError(f"only SELECT queries are allowed, got {type(tree).__name__.upper()}")

    for node in tree.walk():
        name = type(node).__name__
        if name in FORBIDDEN_NODES:
            raise SQLGuardError(f"forbidden operation: {name.upper()}")
        if TABLE_FUNCTION_CLASSES and isinstance(node, TABLE_FUNCTION_CLASSES):
            raise SQLGuardError(f"forbidden function: {name}")
        if isinstance(node, exp.Func):
            fname = node.sql_name() if not isinstance(node, exp.Anonymous) else node.name
            if FORBIDDEN_FUNC_RE.match(str(fname)):
                raise SQLGuardError(f"forbidden function: {fname}")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func) or isinstance(table.this, exp.Anonymous):
            raise SQLGuardError("table functions are not allowed")
        name = table.name.lower()
        if not table.db and name in cte_names:
            continue
        qualified = f"{table.db.lower()}.{name}" if table.db else name
        if qualified not in ALLOWED_TABLES:
            raise SQLGuardError(f"table not allowed: {qualified or '(none)'}")

    limit = tree.args.get("limit")
    current = None
    if limit is not None:
        try:
            current = int(limit.expression.name if hasattr(limit, "expression") and limit.expression else limit.name)
        except (ValueError, TypeError, AttributeError):
            current = None
    if current is None or current > row_limit:
        tree = tree.limit(row_limit)
    return tree.sql(dialect="duckdb")


@dataclass
class SQLResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False


def execute_readonly(db_path: Path, sql: str, timeout_s: float = 5.0, row_limit: int = 100) -> SQLResult:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"ticket store not found at {db_path}; run the Part A pipeline first")
    con = duckdb.connect(
        str(db_path), read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )
    result: dict = {}

    def run():
        try:
            cur = con.execute(sql)
            result["columns"] = [d[0] for d in cur.description]
            result["rows"] = cur.fetchmany(row_limit + 1)
        except Exception as e:  # surfaced below
            result["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        con.interrupt()
        worker.join(1)
        con.close()
        raise TimeoutError(f"query exceeded {timeout_s}s")
    con.close()
    if "error" in result:
        raise result["error"]
    rows = [[_jsonable(mask_value(v)) for v in r] for r in result["rows"]]
    return SQLResult(sql, result["columns"], rows[:row_limit], truncated=len(rows) > row_limit)


def _jsonable(v):
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        return round(v, 2)
    return str(v)  # dates, timestamps, decimals


# ---------------------------------------------------------------------------
# Rule-based SQL writer (used by the mock LLM)
# ---------------------------------------------------------------------------

PRODUCT_PATTERNS = {
    "billing": r"\bbilling\b",
    "mobile_app": r"\bmobile[ _-]?app\b|\bmobile\b",
    "web_portal": r"\bweb[ _-]?portal\b|\bportal\b",
    "api": r"\bapi\b",
    "integrations": r"\bintegrations?\b",
}
PRIORITY_PATTERNS = {p: rf"\b{p}\b" for p in ("urgent", "high", "medium", "low")}
STATUS_PATTERNS = {
    "open": r"\bopen\b", "pending": r"\bpending\b", "in_progress": r"\bin[ _-]progress\b",
    "resolved": r"\bresolved\b", "closed": r"\bclosed\b",
}
GROUP_PATTERNS = {"product": r"\b(by|per|each)\s+product", "priority": r"\b(by|per|each)\s+priority",
                  "status": r"\b(by|per|each)\s+status", "channel": r"\b(by|per|each)\s+channel"}


def _found(patterns: dict[str, str], text: str) -> list[str]:
    return [k for k, p in patterns.items() if re.search(p, text, re.I)]


def _in_list(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class RuleBasedSQLWriter:
    """Maps common support-manager questions to SQL. Returns None when no rule applies."""

    def write(self, question: str) -> str | None:
        q = question.lower()
        products = _found(PRODUCT_PATTERNS, q)
        priorities = _found(PRIORITY_PATTERNS, q)
        statuses = _found(STATUS_PATTERNS, q)
        groups = _found(GROUP_PATTERNS, q)

        m = re.search(r"\btkt-\d{6}\b", q)
        if m:
            return ("SELECT ticket_id, status, priority, product, agent_id, created_at, updated_at "
                    f"FROM clean.tickets WHERE ticket_id = '{m.group().upper()}'")

        if re.search(r"resolution time|time to (resolve|close)|resolve tickets|resolution hours|take to resolve|long .*resolv", q):
            sql = ("SELECT product, resolved_tickets, avg_resolution_hours, median_resolution_hours, "
                   "p90_resolution_hours FROM curated.avg_resolution_by_product")
            if products:
                sql += f" WHERE product IN ({_in_list(products)})"
            if re.search(r"slowest|longest|worst|highest", q):
                return sql + " ORDER BY avg_resolution_hours DESC LIMIT 1"
            if re.search(r"fastest|quickest|shortest|best|lowest", q):
                return sql + " ORDER BY avg_resolution_hours ASC LIMIT 1"
            return sql + " ORDER BY product"

        if "backlog" in q:
            where = []
            if priorities:
                where.append(f"priority IN ({_in_list(priorities)})")
            if re.search(r"\b(current|now|latest|this week|today)\b", q):
                where.append("week_start = (SELECT max(week_start) FROM curated.backlog_by_priority_week)")
            sql = ("SELECT week_start, priority, open_backlog_at_week_end, created_in_week, resolved_in_week "
                   "FROM curated.backlog_by_priority_week")
            if where:
                sql += " WHERE " + " AND ".join(where)
            return sql + " ORDER BY week_start, priority"

        if re.search(r"\bagents?\b", q) and re.search(r"\b(most|top|best|busiest)\b", q):
            return ("SELECT t.agent_id, a.agent_name, count(*) AS resolved_tickets FROM clean.tickets t "
                    "LEFT JOIN clean.agents a USING (agent_id) WHERE t.status IN ('resolved', 'closed') "
                    "GROUP BY t.agent_id, a.agent_name ORDER BY resolved_tickets DESC LIMIT 5")

        if re.search(r"\bunassigned\b|without an? agent|no agent", q):
            return ("SELECT priority, count(*) AS unassigned_open_tickets FROM clean.tickets "
                    "WHERE agent_id IS NULL AND status NOT IN ('resolved', 'closed') GROUP BY priority ORDER BY priority")

        if re.search(r"how many|number of|count|total", q) and re.search(r"\btickets?\b", q):
            where = []
            if products:
                where.append(f"product IN ({_in_list(products)})")
            if priorities:
                where.append(f"priority IN ({_in_list(priorities)})")
            if statuses:
                where.append(f"status IN ({_in_list(statuses)})")
            if groups:
                g = groups[0]
                sql = f"SELECT {g}, count(*) AS tickets FROM clean.tickets"
                if where:
                    sql += " WHERE " + " AND ".join(where)
                return sql + f" GROUP BY {g} ORDER BY tickets DESC"
            sql = "SELECT count(*) AS tickets FROM clean.tickets"
            return sql + (" WHERE " + " AND ".join(where) if where else "")

        return None
