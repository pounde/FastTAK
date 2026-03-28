"""CoT database size query via direct PostgreSQL connection."""

from app.db import query


def get_cot_db_size() -> dict:
    """Query CoT database size."""
    try:
        rows = query("SELECT pg_database_size('cot')")
        size_bytes = int(rows[0][0])
        return {
            "size_bytes": size_bytes,
            "size_human": _human_size(size_bytes),
            "status": (
                "critical"
                if size_bytes > 40_000_000_000
                else "warning"
                if size_bytes > 25_000_000_000
                else "ok"
            ),
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
