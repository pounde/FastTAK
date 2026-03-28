"""CoT database size query via direct PostgreSQL connection."""

from app.db import query


def get_cot_db_size() -> dict:
    """Query CoT database size."""
    try:
        rows = query("SELECT pg_database_size('cot')")
        size_bytes = int(rows[0][0])
        live_rows = query(
            "SELECT COALESCE(SUM(pg_total_relation_size(relid)), 0) FROM pg_stat_user_tables"
        )
        live_bytes = int(live_rows[0][0])
        return {
            "size_bytes": size_bytes,
            "size_human": _human_size(size_bytes),
            "live_bytes": live_bytes,
            "live_human": _human_size(live_bytes),
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
