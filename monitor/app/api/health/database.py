"""CoT database size query via docker exec."""

from app.config import settings
from app.docker_client import find_container


def _get_db_password() -> str:
    return settings.tak_db_password


def get_cot_db_size() -> dict:
    """Query CoT database size via psql inside tak-database container."""
    container = find_container("tak-database")
    if container is None:
        return {"error": "tak-database container not found"}

    try:
        exit_code, output = container.exec_run(
            ["psql", "-h", "localhost", "-U", "martiuser", "-d", "cot",
             "-t", "-A", "-c", "SELECT pg_database_size('cot')"],
            environment={"PGPASSWORD": _get_db_password()},
        )
        if exit_code != 0:
            return {"error": f"psql failed: {output.decode()[:200]}"}

        size_bytes = int(output.decode().strip())
        return {
            "size_bytes": size_bytes,
            "size_human": _human_size(size_bytes),
            "status": (
                "critical" if size_bytes > 40_000_000_000
                else "warning" if size_bytes > 25_000_000_000
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
