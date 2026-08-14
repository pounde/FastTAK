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


VALID_TOKENS_SECRET = "0" * 64
VALID_TAK_VERSION = "5.8-RELEASE-65"


def _run(env_content: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the validator against `env_content`.

    Each test exercises one rule, so a valid TOKENS_API_SECRET is supplied
    unless the content sets one itself — otherwise every fixture would have to
    carry an unrelated key to get past that rule. Tests for the rule itself
    provide their own value.
    """
    if "TOKENS_API_SECRET" not in env_content:
        env_content = f"{env_content}TOKENS_API_SECRET={VALID_TOKENS_SECRET}\n"
    if "TAK_VERSION" not in env_content:
        env_content = f"{env_content}TAK_VERSION={VALID_TAK_VERSION}\n"
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


# ── TOKENS_API_SECRET ──────────────────────────────────────────────────────
# ldap-proxy calls log.Fatal without it and tak-server waits on ldap-proxy, so
# an empty value takes the whole stack down (DD-050). The gate turns that into
# a named key instead of a stack that never comes up.


@pytest.mark.parametrize("mode", MODES)
def test_empty_tokens_secret_fails(tmp_path, mode):
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD=strong-custom-pw-42\nTOKENS_API_SECRET=\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "tokens_api_secret" in result.stderr.lower()


def test_missing_tokens_secret_key_fails(tmp_path):
    """Absent entirely — the state of every .env upgraded from before #54."""
    result = _run(
        "SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=strong-custom-pw-42\nTOKENS_API_SECRET=\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "tokens_api_secret" in result.stderr.lower()


def test_tokens_secret_error_names_the_fix(tmp_path):
    """The message has to say what to run, or it is just a different mystery."""
    result = _run(
        "SERVER_ADDRESS=tak.mydomain.com\nTOKENS_API_SECRET=\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "ensure-secrets.sh" in result.stderr


@pytest.mark.parametrize("mode", MODES)
def test_set_tokens_secret_passes(tmp_path, mode):
    result = _run(
        f"SERVER_ADDRESS=tak.mydomain.com\nDEPLOY_MODE={mode}\n"
        f"TAK_WEBADMIN_PASSWORD=strong-custom-pw-42\n"
        f"TOKENS_API_SECRET={VALID_TOKENS_SECRET}\n",
        tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


# ── TAK_VERSION ────────────────────────────────────────────────────────────


def test_below_floor_tak_version_fails(tmp_path):
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=5.6-RELEASE-6\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "tak_version" in result.stderr.lower()
    assert "5.8" in result.stderr


def test_at_floor_tak_version_passes(tmp_path):
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=5.8-RELEASE-65\n",
        tmp_path,
    )
    assert result.returncode == 0


def test_above_floor_tak_version_passes(tmp_path):
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=5.10-RELEASE-2\n",
        tmp_path,
    )
    assert result.returncode == 0


def test_unset_tak_version_fails(tmp_path):
    """An absent TAK_VERSION leaves compose interpolating an empty image tag."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        f"TAK_WEBADMIN_PASSWORD=secret-pw\nTOKENS_API_SECRET={VALID_TOKENS_SECRET}\n"
    )
    result = subprocess.run(
        ["/bin/bash", str(CHECK), str(env_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "tak_version" in result.stderr.lower()


def test_unparseable_patch_style_tak_version_fails_with_parse_message(tmp_path):
    """5.8.65 looks plausible but isn't the real X.Y[-anything] form — the
    minor substring "8.65" contains a dot and fails the digit-only check.
    This must be reported as unparseable, not as below the floor: 5.8.65 is
    not below 5.8, and telling the operator to find a newer release sends
    them chasing the wrong fix for what is actually a formatting typo.
    """
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=5.8.65\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "5.8.65" in result.stderr
    assert "below the supported floor" not in result.stderr
    stderr_lower = result.stderr.lower()
    assert "pars" in stderr_lower or "format" in stderr_lower


def test_unparseable_garbage_tak_version_fails_with_parse_message(tmp_path):
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=RELEASE-65\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "RELEASE-65" in result.stderr
    assert "below the supported floor" not in result.stderr
    stderr_lower = result.stderr.lower()
    assert "pars" in stderr_lower or "format" in stderr_lower


def test_below_floor_tak_version_still_gets_floor_message_not_parse_message(tmp_path):
    """Regression guard: a genuinely below-floor value must keep getting the
    below-the-floor message, not the new parse-failure message. This is the
    assertion that stops the two branches (unparseable vs. below-floor) from
    being confused again.
    """
    result = _run(
        "SERVER_ADDRESS=tak.internal\nDEPLOY_MODE=direct\n"
        "TAK_WEBADMIN_PASSWORD=secret-pw\nTAK_VERSION=5.6-RELEASE-6\n",
        tmp_path,
    )
    assert result.returncode == 1
    assert "below the supported floor" in result.stderr
    stderr_lower = result.stderr.lower()
    assert "could not be parsed" not in stderr_lower
    assert "could not parse" not in stderr_lower
