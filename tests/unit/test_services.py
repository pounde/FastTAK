"""Tests for dashboard service link generation."""

from unittest.mock import patch

from app.dashboard.services import get_service_links


def test_direct_mode_links():
    """Direct mode builds URLs with ports."""
    mock_settings = {
        "deploy_mode": "direct",
        "server_address": "192.168.1.50",
        "nodered_port": 1880,
        "monitor_port": 8180,
        "takserver_admin_port": 8446,
        "mediamtx_port": 8888,
    }
    with patch("app.dashboard.services.settings") as s:
        for k, v in mock_settings.items():
            setattr(s, k, v)
        links = get_service_links()

    urls = {link["name"]: link["url"] for link in links}
    assert urls["TAK Portal"] == "https://192.168.1.50"
    assert urls["TAK Server"] == "https://192.168.1.50:8446"
    assert urls["Node-RED"] == "https://192.168.1.50:1880"
    assert urls["Monitor"] == "https://192.168.1.50:8180"
    assert urls["MediaMTX"] == "https://192.168.1.50:8888"
    assert "Authentik" not in urls


def test_subdomain_mode_links():
    """Subdomain mode builds URLs with subdomains."""
    mock_settings = {
        "deploy_mode": "subdomain",
        "server_address": "tak.example.com",
        "takserver_subdomain": "takserver",
        "mediamtx_subdomain": "stream",
        "takportal_subdomain": "portal",
        "nodered_subdomain": "nodered",
        "monitor_subdomain": "monitor",
    }
    with patch("app.dashboard.services.settings") as s:
        for k, v in mock_settings.items():
            setattr(s, k, v)
        s.nodered_port = 1880
        s.monitor_port = 8180
        s.takserver_admin_port = 8446
        s.mediamtx_port = 8888
        links = get_service_links()

    urls = {link["name"]: link["url"] for link in links}
    assert urls["TAK Portal"] == "https://portal.tak.example.com"
    assert urls["TAK Server"] == "https://takserver.tak.example.com"
    assert "Authentik" not in urls
