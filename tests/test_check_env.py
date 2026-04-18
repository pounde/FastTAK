# tests/test_check_env.py
"""Preflight .env validator tests.

Covers the rules enforced by scripts/check-env.sh:
- .env must exist at the given path
- SERVER_ADDRESS must be set and not the placeholder
- TAK_WEBADMIN_PASSWORD must not be the documented default (empty is permitted)

Tests parametrize over DEPLOY_MODE to verify the rules are mode-independent.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check-env.sh"

DEFAULT_PASSWORD = "FastTAK-Admin-1!"
MODES = ["direct", "subdomain", ""]  # "" covers an unset DEPLOY_MODE


def _run(env_content: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env_file = tmp_path / ".env"
    env_file.write_text(env_content)
    return subprocess.run(
        ["/bin/bash", str(CHECK), str(env_file)],
        capture_output=True,
        text=True,
    )


def test_missing_env_file_fails(tmp_path):
    result = subprocess.run(
        ["/bin/bash", str(CHECK), str(tmp_path / "nope.env")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr.lower()


def test_unset_server_address_fails(tmp_path):
    result = _run(
        "SERVER_ADDRESS=\nDEPLOY_MODE=subdomain\nTAK_WEBADMIN_PASSWORD=secret-pw\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "server_address" in result.stderr.lower()


def test_placeholder_server_address_fails(tmp_path):
    result = _run(
        "SERVER_ADDRESS=tak.example.com\nDEPLOY_MODE=subdomain\nTAK_WEBADMIN_PASSWORD=secret-pw\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "server_address" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_default_webadmin_password_fails(tmp_path, mode):
    """Documented default is rejected regardless of DEPLOY_MODE."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_empty_webadmin_password_passes(tmp_path, mode):
    """Empty password preserves 'skip webadmin user creation' semantics in all modes."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\nTAK_WEBADMIN_PASSWORD=\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


@pytest.mark.parametrize("mode", MODES)
def test_custom_webadmin_password_passes(tmp_path, mode):
    """Any non-default non-empty password passes in all modes."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD=my-strong-password-42\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
