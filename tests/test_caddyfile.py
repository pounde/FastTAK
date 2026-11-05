"""Renders the Caddyfile from init-config/start.sh and checks the auth routes.

Runs only the "Generate Caddyfile" section of the script, so the assertions see
exactly what Caddy would load in each DEPLOY_MODE.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = (REPO / "init-config" / "start.sh").read_text()

SECTION = re.search(
    r'^CADDY_DIR=.*?^echo "\[init\] Caddyfile generated', SCRIPT, re.MULTILINE | re.DOTALL
).group(0)


def render(tmp_path: Path, mode: str, **env) -> str:
    script = SECTION.rsplit("\n", 1)[0]
    subprocess.run(
        ["sh", "-eu", "-c", script],
        env={
            "PATH": "/usr/bin:/bin",
            "TAK_DIR": str(tmp_path),
            "SERVER_ADDRESS": "tak.example.org",
            "DEPLOY_MODE": mode,
            **env,
        },
        check=True,
        capture_output=True,
        text=True,
    )
    return (tmp_path / "caddy" / "Caddyfile").read_text()


def site_blocks(caddyfile: str) -> dict[str, str]:
    """Top-level site blocks, keyed by address."""
    blocks = {}
    for match in re.finditer(r"^(\S.*?) \{\n(.*?)^\}$", caddyfile, re.MULTILINE | re.DOTALL):
        blocks[match.group(1)] = match.group(2)
    return blocks


@pytest.fixture(params=["direct", "subdomain"])
def authed_routes(request, tmp_path) -> list[str]:
    blocks = site_blocks(render(tmp_path, request.param))
    routes = [body for body in blocks.values() if "forward_auth" in body]
    assert len(routes) == 2, "expected the Node-RED and monitor routes"
    return routes


def test_client_supplied_identity_headers_are_dropped_before_auth(authed_routes):
    for route in authed_routes:
        strip_user = route.index("request_header -Remote-User")
        strip_groups = route.index("request_header -Remote-Groups")
        assert max(strip_user, strip_groups) < route.index("forward_auth")


def test_client_supplied_forwarded_address_is_never_trusted(authed_routes):
    """ldap-proxy locks out by the address Caddy forwards. With trusted_proxies,
    a client seen from a private range chooses that address itself (#62)."""
    for route in authed_routes:
        assert "trusted_proxies" not in route


def test_backends_never_receive_the_ldap_password(authed_routes):
    for route in authed_routes:
        assert re.search(r"reverse_proxy \S+ \{[^}]*header_up -Authorization", route)
