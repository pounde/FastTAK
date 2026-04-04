"""Shared test fixtures for FastTAK monitor tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add monitor/ to sys.path so tests can `from app.* import ...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monitor"))


@pytest.fixture
def mock_settings(monkeypatch):
    """Patch app.config.settings with test defaults."""
    from app.config import Settings

    test_settings = Settings(
        server_address="test.example.com",
        tak_db_password="testpass",
        tak_db_url="",
        authentik_url="http://authentik-test:9000",
        authentik_api_token="test-token",
        tak_server_url="https://tak-test:8443",
        tak_api_cert_path="/tmp/test.p12",
        tak_api_cert_password="testpass",
        users_hidden_prefixes="ak-,adm_,svc_,ma-",
        user_expiry_check_interval=60,
        enrollment_ttl_minutes=15,
        tak_enrollment_port=8446,
    )
    monkeypatch.setattr("app.config.settings", test_settings)
    return test_settings


@pytest.fixture
def mock_docker_client(monkeypatch):
    """Patch docker.DockerClient with a configurable fake."""
    client = MagicMock()
    monkeypatch.setattr("app.docker_client._client", client)
    monkeypatch.setattr("app.docker_client._cached_services", [])
    monkeypatch.setattr("app.docker_client._cache_time", 0)
    return client


def make_fake_container(
    name, status="running", health="healthy", image_tag="test:latest", labels=None
):
    """Create a fake Docker container object for testing."""
    container = MagicMock()
    container.name = f"fastak-{name}-1"
    container.status = status
    container.labels = labels or {
        "com.docker.compose.service": name,
        "com.docker.compose.project": "fastak",
    }
    container.attrs = {
        "State": {
            "Health": {"Status": health} if health != "none" else {},
        }
    }
    image = MagicMock()
    image.tags = [image_tag] if image_tag else []
    container.image = image
    return container
