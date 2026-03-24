"""Thin wrapper around Docker SDK. All Docker API access goes through here."""

import threading

import docker

_lock = threading.Lock()
_client = None


def get_client() -> docker.DockerClient:
    global _client
    with _lock:
        if _client is None:
            _client = docker.DockerClient.from_env()
        return _client


# Container names in the FastTAK compose stack
FASTTAK_CONTAINERS = [
    "tak-server",
    "tak-database",
    "caddy",
    "authentik-server",
    "authentik-worker",
    "authentik-ldap",
    "redis",
    "app-db",
    "tak-portal",
    "mediamtx",
    "nodered",
]


def find_container(name: str):
    """Find a container by its compose service name (handles project name prefixes)."""
    client = get_client()
    matches = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.service={name}"},
    )
    return matches[0] if matches else None
