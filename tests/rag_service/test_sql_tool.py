import pytest

from rag_service.router import route
from rag_service.sql_tool import RuleBasedSQLWriter, SQLGuardError, execute_readonly, guard_sql

ATTACKS = [
    "DELETE FROM clean.tickets",
    "UPDATE clean.tickets SET status = 'closed'",
    "DROP TABLE clean.tickets",
    "SELECT 1; DROP TABLE clean.tickets",
    "SELECT * FROM raw.tickets",
    "SELECT * FROM clean.tickets_quarantine",
    "SELECT * FROM meta.ingest_log",
    "SELECT * FROM clean.customers",
    "SELECT * FROM (SELECT * FROM clean.customers) x",
    "WITH x AS (SELECT * FROM raw.tickets) SELECT * FROM x",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT getenv('HOME')",
    "COPY clean.tickets TO '/tmp/out.csv'",
    "ATTACH '/tmp/other.db' AS other",
    "PRAGMA database_list",
    "INSTALL httpfs",
    "",
    "not sql at all ;;",
]


@pytest.mark.parametrize("sql", ATTACKS)
def test_guard_blocks(sql):
    with pytest.raises(SQLGuardError):
        guard_sql(sql)


def test_guard_allows_select_and_forces_limit():
    assert guard_sql("SELECT count(*) FROM clean.tickets").endswith("LIMIT 100")
    assert guard_sql("SELECT * FROM clean.tickets LIMIT 100000", row_limit=50).endswith("LIMIT 50")
    assert guard_sql("SELECT * FROM clean.tickets LIMIT 5").endswith("LIMIT 5")
    assert "curated.backlog_by_priority_week" in guard_sql(
        "WITH b AS (SELECT * FROM curated.backlog_by_priority_week) SELECT * FROM b")


def test_execute_is_read_only_even_without_guard(ticket_db):
    with pytest.raises(Exception):
        execute_readonly(ticket_db, "DELETE FROM clean.tickets")
    with pytest.raises(Exception):
        execute_readonly(ticket_db, "SELECT * FROM read_csv('/etc/passwd')")


def test_execute_masks_pii(ticket_db):
    res = execute_readonly(ticket_db, guard_sql("SELECT resolution_text FROM clean.tickets WHERE ticket_id='TKT-000001'"))
    assert res.rows == [["Mailed [EMAIL]"]]


@pytest.mark.parametrize("question,expected_fragment", [
    ("What is the average resolution time per product?", "curated.avg_resolution_by_product"),
    ("Which product has the slowest resolution time?", "ORDER BY avg_resolution_hours DESC LIMIT 1"),
    ("What is the current backlog of urgent tickets?", "priority IN ('urgent')"),
    ("How many open high priority billing tickets are there?", "status IN ('open')"),
    ("How many tickets by priority?", "GROUP BY priority"),
    ("What is the status of TKT-000002?", "ticket_id = 'TKT-000002'"),
    ("Which agents resolved the most tickets?", "resolved_tickets DESC"),
])
def test_rule_writer_produces_guarded_sql(question, expected_fragment, ticket_db):
    sql = RuleBasedSQLWriter().write(question)
    assert expected_fragment in sql
    execute_readonly(ticket_db, guard_sql(sql))  # runs without error


def test_rule_writer_returns_none_when_unsure():
    assert RuleBasedSQLWriter().write("What is the weather like?") is None


@pytest.mark.parametrize("question,expected", [
    ("How do I enable CORS?", "docs"),
    ("How many Uvicorn workers should I run?", "docs"),
    ("What is the backlog of urgent tickets?", "tickets"),
    ("Average resolution time for billing?", "tickets"),
    ("What is the status of TKT-000123?", "tickets"),
    ("How many billing issues per week?", "tickets"),
])
def test_router(question, expected):
    assert route(question) == expected
