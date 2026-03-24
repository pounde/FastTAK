"""API test fixtures — TestClient with patched lifespan."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient with scheduler and config_hash patched out."""
    with (
        patch("app.main.init_config_hash"),
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler"),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c
