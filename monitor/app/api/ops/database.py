"""Database maintenance operations."""

from app.config import settings
from app.docker_client import find_container


def vacuum_database(full: bool = False) -> dict:
    """Run VACUUM on the CoT database."""
    container = find_container("tak-database")
    if container is None:
        return {"success": False, "error": "tak-database container not found"}

    cmd = "VACUUM FULL ANALYZE" if full else "VACUUM ANALYZE"
    try:
        exit_code, output = container.exec_run(
            ["psql", "-h", "localhost", "-U", "martiuser", "-d", "cot", "-c", cmd],
            environment={"PGPASSWORD": settings.tak_db_password},
        )
        return {
            "success": exit_code == 0,
            "command": cmd,
            "output": output.decode(errors="replace")[:500],
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}
