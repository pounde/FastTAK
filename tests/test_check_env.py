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


@pytest.mark.parametrize("mode", MODES)
def test_quoted_default_password_fails(tmp_path, mode):
    """Docker Compose strips quotes on load — quoted default must also be rejected."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f'TAK_WEBADMIN_PASSWORD="{DEFAULT_PASSWORD}"\n',
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_single_quoted_default_password_fails(tmp_path, mode):
    """Same bypass with single quotes."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD='{DEFAULT_PASSWORD}'\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


def test_quoted_placeholder_server_address_fails(tmp_path):
    """Quoted placeholder SERVER_ADDRESS must also be rejected."""
    result = _run(
        'SERVER_ADDRESS="tak.example.com"\nDEPLOY_MODE=subdomain\n'
        "TAK_WEBADMIN_PASSWORD=custom-pw-42\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "server_address" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_duplicate_key_last_wins_default_fails(tmp_path, mode):
    """Duplicate key: last wins (Compose dotenv semantics). Default on last line is rejected."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD=real-password-99\n"
        f"TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_duplicate_key_last_wins_custom_passes(tmp_path, mode):
    """Duplicate key: last value wins. Custom on last line passes even if default appears first."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}\n"
        f"TAK_WEBADMIN_PASSWORD=real-password-99\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


@pytest.mark.parametrize("mode", MODES)
def test_password_containing_equals_passes(tmp_path, mode):
    """Password with `=` in it must be read in full, not truncated at the first `=`."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD=abc=def=ghi\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


@pytest.mark.parametrize("mode", MODES)
def test_leading_whitespace_default_fails(tmp_path, mode):
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"  TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_export_prefix_default_fails(tmp_path, mode):
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"export TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_trailing_whitespace_default_fails(tmp_path, mode):
    """Docker Compose trims trailing whitespace on unquoted values."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD={DEFAULT_PASSWORD}   \n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_inline_comment_default_fails(tmp_path, mode):
    """Compose treats # after quoted values as a comment; validator must too."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f'TAK_WEBADMIN_PASSWORD="{DEFAULT_PASSWORD}"  # inline comment\n',
        tmp_path,
    )
    assert result.returncode == 1
    assert "default" in result.stderr.lower()


@pytest.mark.parametrize("mode", MODES)
def test_export_prefix_custom_passes(tmp_path, mode):
    """Export prefix with a custom password should still pass."""
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"export TAK_WEBADMIN_PASSWORD=strong-custom-pw-42\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_missing_webadmin_password_key_passes(tmp_path):
    """Entirely missing TAK_WEBADMIN_PASSWORD key = treated as empty = passes (skip webadmin)."""
    result = _run(
        "SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE=direct\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
