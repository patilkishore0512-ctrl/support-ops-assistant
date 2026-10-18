"""
Curated views. Views (not tables) so they are always consistent with clean.* and
need no separate refresh or idempotency handling.

Definitions
  resolved_at   first time a ticket reached resolved/closed AFTER its last unresolved
                version (so a reopened ticket's earlier resolution is not counted).
                Only set if the ticket is currently resolved/closed.
  resolution    resolved_at - created_at, in hours.
  backlog       at the end of week W, a ticket is in the backlog if it was created
                before the week ended and was not resolved before the week ended.
"""

CURATED_SQL = """
CREATE OR REPLACE VIEW curated.ticket_resolution AS
WITH last_unresolved AS (
    SELECT ticket_id, max(updated_at) AS last_unresolved_at
    FROM clean.ticket_versions
    WHERE status NOT IN ('resolved', 'closed')
    GROUP BY ticket_id
),
first_resolution AS (
    SELECT v.ticket_id, min(v.updated_at) AS resolved_at
    FROM clean.ticket_versions v
    LEFT JOIN last_unresolved u USING (ticket_id)
    WHERE v.status IN ('resolved', 'closed')
      AND (u.last_unresolved_at IS NULL OR v.updated_at > u.last_unresolved_at)
    GROUP BY v.ticket_id
)
SELECT
    t.ticket_id, t.product, t.priority, t.status, t.created_at,
    CASE WHEN t.status IN ('resolved', 'closed') THEN r.resolved_at END AS resolved_at,
    CASE WHEN t.status IN ('resolved', 'closed')
         THEN date_diff('minute', t.created_at, r.resolved_at) / 60.0 END AS resolution_hours
FROM clean.tickets t
LEFT JOIN first_resolution r USING (ticket_id);

CREATE OR REPLACE VIEW curated.avg_resolution_by_product AS
SELECT
    product,
    count(*)                                         AS resolved_tickets,
    round(avg(resolution_hours), 2)                  AS avg_resolution_hours,
    round(median(resolution_hours), 2)               AS median_resolution_hours,
    round(quantile_cont(resolution_hours, 0.9), 2)   AS p90_resolution_hours
FROM curated.ticket_resolution
WHERE resolution_hours IS NOT NULL
GROUP BY product
ORDER BY product;

CREATE OR REPLACE VIEW curated.backlog_by_priority_week AS
WITH bounds AS (
    SELECT date_trunc('week', min(created_at)) AS first_week,
           date_trunc('week', max(greatest(created_at, coalesce(resolved_at, created_at)))) AS last_week
    FROM curated.ticket_resolution
),
weeks AS (
    SELECT unnest(generate_series(first_week, last_week, INTERVAL 7 DAY)) AS week_start FROM bounds
),
priorities AS (
    SELECT unnest(['urgent', 'high', 'medium', 'low']) AS priority
)
SELECT
    CAST(w.week_start AS DATE)                                   AS week_start,
    p.priority,
    count(t.ticket_id) FILTER (WHERE t.created_at < w.week_start + INTERVAL 7 DAY
                                 AND (t.resolved_at IS NULL OR t.resolved_at >= w.week_start + INTERVAL 7 DAY))
                                                                 AS open_backlog_at_week_end,
    count(t.ticket_id) FILTER (WHERE t.created_at >= w.week_start
                                 AND t.created_at <  w.week_start + INTERVAL 7 DAY) AS created_in_week,
    count(t.ticket_id) FILTER (WHERE t.resolved_at >= w.week_start
                                 AND t.resolved_at <  w.week_start + INTERVAL 7 DAY) AS resolved_in_week
FROM weeks w
CROSS JOIN priorities p
LEFT JOIN curated.ticket_resolution t ON t.priority = p.priority
GROUP BY w.week_start, p.priority
ORDER BY w.week_start, CASE p.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END;

CREATE OR REPLACE VIEW curated.dq_summary AS
SELECT file_name, check_name, check_type, severity, total_rows, failed_rows, passed
FROM meta.dq_results
ORDER BY file_name, check_type DESC, failed_rows DESC, check_name;
"""


def create_curated_views(con) -> None:
    con.execute(CURATED_SQL)
