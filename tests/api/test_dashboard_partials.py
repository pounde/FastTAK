"""The cert and TLS partials render what the probes return: a status per row
from the same thresholds the service status uses, and the reason for a row
the probe could not read (#57)."""

import pytest
from app import store

THRESHOLDS = {"days_left": {"warning": 30, "critical": 7}}


@pytest.fixture(autouse=True)
def _clear_health_store():
    yield
    with store._lock:
        store._cache.pop("certs", None)
        store._cache.pop("tls", None)


def test_cert_rows_carry_status_and_reason(client):
    store.update(
        "certs",
        {
            "items": [
                {
                    "file": "ca.pem",
                    "subject": "CN=ca",
                    "expires": "2030-01-01",
                    "days_left": 900,
                    "category": "infrastructure",
                },
                {
                    "file": "takserver.pem",
                    "subject": "CN=tak",
                    "expires": "2026-09-20",
                    "days_left": 5,
                    "category": "infrastructure",
                },
                {
                    "file": "svc_bot.pem",
                    "error": "openssl: unable to load certificate",
                    "category": "service",
                },
            ]
        },
        {"status": "critical", "message": "takserver.pem: days_left is 5 (threshold: 7)"},
        THRESHOLDS,
    )
    html = client.get("/ui/partials/cert-status").text
    assert "badge-green" in html and "badge-red" in html and "badge-yellow" in html
    assert "openssl: unable to load certificate" in html
    assert ">ok<" in html and ">critical<" in html and ">warning<" in html


def test_tls_rows_carry_status_and_reason(client):
    store.update(
        "tls",
        {
            "items": [
                {"domain": "takserver.example.com", "expires": "2026-12-01", "days_left": 77},
                {"domain": "nodered.example.com", "error": "ConnectionRefusedError"},
            ]
        },
        {"status": "warning", "message": "nodered.example.com: ConnectionRefusedError"},
        THRESHOLDS,
    )
    html = client.get("/ui/partials/tls-status").text
    assert "takserver.example.com" in html and "nodered.example.com" in html
    assert "ConnectionRefusedError" in html
    assert "badge-green" in html and "badge-yellow" in html


def test_tls_partial_without_a_snapshot_says_so(client):
    with store._lock:
        store._cache.pop("tls", None)
    html = client.get("/ui/partials/tls-status").text
    assert "No TLS endpoints" in html


def test_cert_panel_shows_a_probe_level_error(client):
    store.update(
        "certs",
        {"error": "openssl not found in the monitor image"},
        {"status": "critical", "message": "openssl not found in the monitor image"},
        THRESHOLDS,
    )
    html = client.get("/ui/partials/cert-status").text
    assert "openssl not found in the monitor image" in html
    assert "No certificates found" not in html


def test_cert_days_left_color_follows_status_not_a_literal(client):
    """The Days Left cell used to hardcode <=7/<=30 regardless of the
    configured thresholds; it must follow cert.status instead, so a service
    with different thresholds still colors correctly."""
    store.update(
        "certs",
        {
            "items": [
                {
                    "file": "ca.pem",
                    "days_left": 60,
                    "category": "infrastructure",
                }
            ]
        },
        {"status": "warning", "message": "ca.pem: days_left is 60 (threshold: 100)"},
        {"days_left": {"warning": 100, "critical": 50}},
    )
    html = client.get("/ui/partials/cert-status").text
    assert 'class="text-yellow"' in html


def test_cert_note_status_renders_a_gray_badge(client):
    store.update(
        "certs",
        {
            "items": [
                {
                    "file": "svc_bot.pem",
                    "error": "openssl: unable to load certificate",
                    "category": "service",
                }
            ]
        },
        {"status": "note", "message": "svc_bot.pem: openssl: unable to load certificate"},
        {"error_status": "note"},
    )
    html = client.get("/ui/partials/cert-status").text
    assert "badge-gray" in html and ">note<" in html


def test_tls_note_status_renders_a_gray_badge(client):
    store.update(
        "tls",
        {"items": [{"domain": "nodered.example.com", "error": "ConnectionRefusedError"}]},
        {"status": "note", "message": "nodered.example.com: ConnectionRefusedError"},
        {"error_status": "note"},
    )
    html = client.get("/ui/partials/tls-status").text
    assert "badge-gray" in html and ">note<" in html


def test_tls_panel_shows_a_probe_level_error(client):
    store.update(
        "tls",
        {"error": "DNS resolution failed for the configured SERVER_ADDRESS"},
        {
            "status": "critical",
            "message": "DNS resolution failed for the configured SERVER_ADDRESS",
        },
        THRESHOLDS,
    )
    html = client.get("/ui/partials/tls-status").text
    assert "DNS resolution failed for the configured SERVER_ADDRESS" in html
    assert "No TLS endpoints" not in html
