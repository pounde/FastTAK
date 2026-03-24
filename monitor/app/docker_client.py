"""Thin wrapper around Docker SDK. All Docker API access goes through here."""

import threading
import time

import docker

_lock = threading.Lock()
_client = None


def get_client() -> docker.DockerClient:
    global _client
    with _lock:
        if _client is None:
            _client = docker.DockerClient.from_env()
        return _client


# ── Service discovery ────────────────────────────────────────────────────

_CACHE_TTL = 30  # seconds — avoid hitting Docker API on every request
_cache_lock = threading.Lock()
_cached_services: list[str] = []
_cache_time: float = 0


def _refresh_cache() -> list[str]:
    """Query Docker for all compose services in our project."""
    global _cached_services, _cache_time
    client = get_client()

    # Find the compose project name from our own container's labels
    self_containers = client.containers.list(
        filters={"label": "com.docker.compose.service=monitor"}
    )
    if not self_containers:
        return []
    project = self_containers[0].labels.get("com.docker.compose.project", "")

    # Discover all services in the same project (including stopped/exited)
    containers = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.project={project}"},
    )
    services = sorted(
        {
            c.labels["com.docker.compose.service"]
            for c in containers
            if "com.docker.compose.service" in c.labels
        }
    )

    with _cache_lock:
        _cached_services = services
        _cache_time = time.monotonic()
    return services


def discover_services() -> list[str]:
    """Return all compose service names (including stopped/exited init containers)."""
    with _cache_lock:
        if _cached_services and (time.monotonic() - _cache_time) < _CACHE_TTL:
            return list(_cached_services)
    return _refresh_cache()


def discover_running_services() -> list[str]:
    """Return only services with a currently running container."""
    return [name for name in discover_services() if _is_running(name)]


def _is_running(name: str) -> bool:
    container = find_container(name)
    return container is not None and container.status == "running"


def find_container(name: str):
    """Find a container by its compose service name (handles project name prefixes)."""
    client = get_client()
    matches = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.service={name}"},
    )
    return matches[0] if matches else None
