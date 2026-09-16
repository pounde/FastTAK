"""The compose files publish what stack_expected_published_ports says they do:
UDP 443 for the HTTP/3 Caddy advertises (#122), and a UDP twin for each
direct-mode UI port."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _ports(compose_file: str, service: str) -> list[str]:
    data = yaml.safe_load((REPO / compose_file).read_text())
    return data["services"][service]["ports"]


def test_caddy_publishes_udp_443_for_http3():
    ports = _ports("docker-compose.yml", "caddy")
    assert "443:443" in ports
    assert "443:443/udp" in ports


def test_direct_mode_ui_ports_have_udp_twins():
    ports = _ports("docker-compose.direct.yml", "caddy")
    for var, default in (
        ("NODERED_PORT", "1880"),
        ("MONITOR_PORT", "8180"),
        ("MEDIAMTX_PORT", "8888"),
    ):
        tcp = f"${{{var}:-{default}}}:${{{var}:-{default}}}"
        assert tcp in ports, tcp
        assert f"{tcp}/udp" in ports, f"{tcp}/udp"
