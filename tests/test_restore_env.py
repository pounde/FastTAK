"""tests-integration/restore-env.sh — the .env step of a restore.

The archive's .env must be restored: its database passwords match the role
hashes in the dumps. But TAK_VERSION names the images setup.sh built on
*this* host, not the data, so copying it wholesale rolled the host back to
whatever the archive held (#99). An archive from below the supported floor
is refused before anything destructive happens.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tests-integration" / "restore-env.sh"

HOST_VERSION = "5.8-RELEASE-70"
ARCHIVE_VERSION = "5.8-RELEASE-65"


def _write(path: Path, version: str | None, **extra: str) -> Path:
    lines = [f"{k}={v}" for k, v in extra.items()]
    if version is not None:
        lines.append(f"TAK_VERSION={version}")
    path.write_text("\n".join(lines) + "\n")
    return path


def _run(archive: Path, host: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), str(archive), str(host)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _value(path: Path, key: str) -> str:
    out = subprocess.run(
        ["/bin/bash", "-c", f'. "{REPO}/scripts/lib-env.sh"; env_get "{path}" {key}'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out


def test_secrets_come_from_the_archive_and_the_version_from_the_host(tmp_path):
    archive = _write(tmp_path / "archive.env", ARCHIVE_VERSION, TAK_DB_PASSWORD="from-archive")
    host = _write(tmp_path / ".env", HOST_VERSION, TAK_DB_PASSWORD="fresh-and-wrong")
    result = _run(archive, host)
    assert result.returncode == 0, result.stderr
    assert _value(host, "TAK_DB_PASSWORD") == "from-archive"
    assert _value(host, "TAK_VERSION") == HOST_VERSION
    assert HOST_VERSION in result.stdout and ARCHIVE_VERSION in result.stdout


def test_same_version_is_a_plain_copy(tmp_path):
    archive = _write(tmp_path / "archive.env", HOST_VERSION, TAK_DB_PASSWORD="from-archive")
    host = _write(tmp_path / ".env", HOST_VERSION, TAK_DB_PASSWORD="fresh")
    assert _run(archive, host).returncode == 0
    assert host.read_text() == archive.read_text()


def test_archive_below_the_floor_is_refused_before_touching_the_host(tmp_path):
    archive = _write(tmp_path / "archive.env", "5.6-RELEASE-6", TAK_DB_PASSWORD="old")
    host = _write(tmp_path / ".env", HOST_VERSION, TAK_DB_PASSWORD="fresh")
    before = host.read_text()
    result = _run(archive, host)
    assert result.returncode == 1
    assert host.read_text() == before, "a refused restore must leave the host .env alone"
    assert "5.6-RELEASE-6" in result.stderr
    assert "5.8" in result.stderr  # the floor
    assert "fasttak_version" in result.stderr  # points at the manifest field to check out


def test_archive_without_a_version_gets_the_host_one(tmp_path):
    """Archives from before TAK_VERSION was in .env restore onto a host that
    check-env.sh will otherwise refuse for an unset TAK_VERSION."""
    archive = _write(tmp_path / "archive.env", None, TAK_DB_PASSWORD="from-archive")
    host = _write(tmp_path / ".env", HOST_VERSION, TAK_DB_PASSWORD="fresh")
    assert _run(archive, host).returncode == 0
    assert _value(host, "TAK_VERSION") == HOST_VERSION
    assert _value(host, "TAK_DB_PASSWORD") == "from-archive"


def test_host_without_a_version_keeps_the_archive_one(tmp_path):
    archive = _write(tmp_path / "archive.env", ARCHIVE_VERSION, TAK_DB_PASSWORD="from-archive")
    host = _write(tmp_path / ".env", "", TAK_DB_PASSWORD="fresh")
    assert _run(archive, host).returncode == 0
    assert _value(host, "TAK_VERSION") == ARCHIVE_VERSION


@pytest.mark.parametrize("args", [[], ["only-one"]])
def test_wrong_argument_count_prints_usage(args):
    result = subprocess.run(["/bin/bash", str(SCRIPT), *args], capture_output=True, text=True)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_help():
    result = subprocess.run(["/bin/bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
