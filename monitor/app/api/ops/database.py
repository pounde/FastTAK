"""Database maintenance operations via direct PostgreSQL connection."""

from app.db import execute


def vacuum_database() -> dict:
    """Run VACUUM FULL ANALYZE on the CoT database."""
    try:
        execute("VACUUM FULL ANALYZE")
        return {"success": True, "command": "VACUUM FULL ANALYZE"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}
