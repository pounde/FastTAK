"""Container health status via Docker API."""

from app.docker_client import discover_services, find_container


def get_all_container_health() -> list[dict]:
    """Return health status for all FastTAK containers."""
    results = []
    for name in discover_services():
        container = find_container(name)
        if container is None:
            results.append({"name": name, "status": "not_found", "health": "unknown"})
            continue

        health = "unknown"
        if container.attrs.get("State", {}).get("Health"):
            health = container.attrs["State"]["Health"].get("Status", "unknown")

        results.append(
            {
                "name": name,
                "status": container.status,  # running, exited, etc.
                "health": health,  # healthy, unhealthy, starting, none
                "image": container.image.tags[0] if container.image.tags else "",
            }
        )
    return results


def get_container_stats(name: str) -> dict | None:
    """Return CPU/memory stats for a single container."""
    container = find_container(name)
    if container is None or container.status != "running":
        return None
    try:
        stats = container.stats(stream=False)
        # Calculate CPU % — keys may be missing on freshly started containers
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        )
        num_cpus = stats["cpu_stats"].get("online_cpus", 1)
        cpu_pct = (cpu_delta / system_delta) * num_cpus * 100.0 if system_delta > 0 else 0.0

        mem_usage = stats["memory_stats"].get("usage", 0)
        mem_limit = stats["memory_stats"].get("limit", 1)

        return {
            "name": name,
            "cpu_percent": round(cpu_pct, 2),
            "memory_mb": round(mem_usage / 1024 / 1024, 1),
            "memory_limit_mb": round(mem_limit / 1024 / 1024, 1),
            "memory_percent": round((mem_usage / mem_limit) * 100, 1) if mem_limit else 0,
        }
    except (KeyError, TypeError):
        return None
