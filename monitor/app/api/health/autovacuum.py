"""Autovacuum health check via direct PostgreSQL connection."""

from app.db import query

_AUTOVACUUM_SQL = """\
SELECT relname, n_dead_tup, n_live_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;
"""


def get_autovacuum_health() -> dict:
    """Query autovacuum status for all user tables."""
    try:
        rows = query(_AUTOVACUUM_SQL)
    except Exception as e:
        return {"error": str(e)[:200]}

    items = []

    for row in rows:
        relname, dead, live, dead_pct = row
        relname = relname.decode() if isinstance(relname, bytes) else str(relname)
        dead_pct = float(dead_pct) if dead_pct is not None else 0.0
        items.append(
            {
                "table": relname,
                "dead_tuples": dead,
                "live_tuples": live,
                "dead_pct": dead_pct,
            }
        )

    return {"items": items}
