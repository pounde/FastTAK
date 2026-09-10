"""scripts/lib-stack.sh — one resolution of the compose files and the image
labels, for every entry point.

The value under test is the exported COMPOSE_FILE (or its absence), not
agreement between callers: two callers agreeing on the wrong answer is how
the override auto-load got disabled.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib-stack.sh"

OVERRIDES = [None, "docker-compose.override.yml", "compose.override.yml", "compose.override.yaml"]
MODES = ["direct", "subdomain", ""]


def _resolve(tmp_path: Path, mode: str, capture: bool, override: str | None) -> str:
    (tmp_path / ".env").write_text(f"DEPLOY_MODE={mode}\n")
    if override:
        (tmp_path / override).write_text("services: {}\n")
    flag = "--capture" if capture else ""
    script = (
        f'. "{LIB}"; stack_export_compose_file {flag}; printf "%s" "${{COMPOSE_FILE-<unset>}}"'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _expected(mode: str, capture: bool, override: str | None) -> str:
    files: list[str] = []
    if mode == "direct":
        files = ["docker-compose.yml", "docker-compose.direct.yml"]
    if capture:
        files = (files or ["docker-compose.yml"]) + ["docker-compose.capture.yml"]
    if not files:
        return "<unset>"
    if override:
        files.append(override)
    return ":".join(files)


@pytest.mark.parametrize("override", OVERRIDES)
@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("mode", MODES)
def test_compose_file_resolution(tmp_path, mode, capture, override):
    assert _resolve(tmp_path, mode, capture, override) == _expected(mode, capture, override)


def test_plain_subdomain_leaves_compose_file_unset(tmp_path):
    """The case that protects Compose's own override auto-load."""
    assert _resolve(tmp_path, "subdomain", False, "docker-compose.override.yml") == "<unset>"


def test_quoted_deploy_mode_is_read_like_compose_reads_it(tmp_path):
    (tmp_path / ".env").write_text('DEPLOY_MODE="direct"\n')
    script = f'. "{LIB}"; stack_export_compose_file; printf "%s" "$COMPOSE_FILE"'
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.stdout == "docker-compose.yml:docker-compose.direct.yml"


def test_env_file_override(tmp_path):
    other = tmp_path / "elsewhere.env"
    other.write_text("DEPLOY_MODE=direct\n")
    script = f'. "{LIB}"; stack_export_compose_file; printf "%s" "$COMPOSE_FILE"'
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "FASTAK_ENV_FILE": str(other)},
    )
    assert result.stdout == "docker-compose.yml:docker-compose.direct.yml"


def test_version_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "9.9.9"\n')
    script = (
        f'. "{LIB}"; stack_export_version; printf "%s %s" "$FASTTAK_VERSION" "$FASTTAK_COMMIT"'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    version, commit = result.stdout.split()
    assert version == "9.9.9"
    assert commit == "unknown"  # tmp_path is not a git repository


def test_version_falls_back_to_dev(tmp_path):
    script = f'. "{LIB}"; stack_export_version; printf "%s" "$FASTTAK_VERSION"'
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.stdout == "dev"
