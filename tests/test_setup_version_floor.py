# tests/test_setup_version_floor.py
"""setup.sh refuses TAK Server bundles below the 5.8 hardened floor.

A 5.6 bundle would half-work: the build succeeds, but PGDATA lands at
/var/lib/postgresql/15/data instead of the volume mount, silently
un-persisting the deployment. Refusing is safer than tolerating.

These tests stop before any `docker build` — they assert the floor check
fires first, so they need no Docker daemon.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "setup.sh"


def _fake_bundle(tmp_path: Path, version: str) -> Path:
    """Build a minimal takserver-docker-* tree and zip it."""
    root = tmp_path / "src" / f"takserver-docker-hardened-{version}"
    (root / "tak").mkdir(parents=True)
    (root / "docker").mkdir(parents=True)
    (root / "tak" / "version.txt").write_text(f"{version}\n")
    (root / "docker" / "Dockerfile.hardened-takserver").write_text("FROM scratch\n")
    (root / "docker" / "Dockerfile.hardened-takserver-db").write_text("FROM scratch\n")

    zip_path = tmp_path / f"takserver-docker-hardened-{version}.zip"
    subprocess.run(
        ["zip", "-q", "-r", str(zip_path), f"takserver-docker-hardened-{version}"],
        cwd=tmp_path / "src",
        check=True,
    )
    return zip_path


def _run_setup(zip_path: Path, target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SETUP), "-d", str(target), str(zip_path)],
        capture_output=True,
        text=True,
    )


def test_rejects_5_6_bundle(tmp_path):
    zip_path = _fake_bundle(tmp_path, "5.6-RELEASE-6")
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "5.6" in combined
    assert "5.8" in combined


def test_rejects_bundle_without_hardened_dockerfiles(tmp_path):
    zip_path = _fake_bundle(tmp_path, "5.8-RELEASE-65")
    # Strip the hardened Dockerfiles back out, leaving the legacy names.
    subprocess.run(
        [
            "zip",
            "-q",
            "-d",
            str(zip_path),
            "takserver-docker-hardened-5.8-RELEASE-65/docker/Dockerfile.hardened-takserver",
            "takserver-docker-hardened-5.8-RELEASE-65/docker/Dockerfile.hardened-takserver-db",
        ],
        check=True,
    )
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0
    assert "hardened" in (result.stdout + result.stderr).lower()


def _bundle_with_version_text(tmp_path: Path, text: str) -> Path:
    """A bundle whose tak/version.txt holds `text` verbatim."""
    zip_path = _fake_bundle(tmp_path, "5.6-RELEASE-6")
    # Overwrite version.txt inside the zip.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    subprocess.run(["unzip", "-q", str(zip_path), "-d", str(scratch)], check=True)
    vt = scratch / "takserver-docker-hardened-5.6-RELEASE-6" / "tak" / "version.txt"
    vt.write_text(f"{text}\n")
    zip_path.unlink()
    subprocess.run(
        ["zip", "-q", "-r", str(zip_path), "takserver-docker-hardened-5.6-RELEASE-6"],
        cwd=scratch,
        check=True,
    )
    return zip_path


def test_rejects_unparseable_version(tmp_path):
    zip_path = _bundle_with_version_text(tmp_path, "not-a-version")
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0


@pytest.mark.parametrize("version", ["not-a-version", "5.8.1-RELEASE-3", "v5.8"])
def test_unparseable_version_is_not_reported_as_below_the_floor(tmp_path, version):
    """A version the parser cannot read is a different failure from an old
    bundle, and "below the supported floor of 5.8" sends the operator hunting
    for a newer release they may already have. 5.8.1-RELEASE-3 is the case that
    matters: it is not old, it is three-component. check-env.sh draws this
    distinction already; setup.sh routed everything through the floor check."""
    zip_path = _bundle_with_version_text(tmp_path, version)
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "could not be parsed" in combined
    assert "below the supported floor" not in combined


def test_a_genuinely_old_bundle_still_reports_the_floor(tmp_path):
    """The other side of the same distinction: 5.6 parses fine, so it must
    still be reported as below the floor and not as a parse failure."""
    zip_path = _fake_bundle(tmp_path, "5.6-RELEASE-6")
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "below the supported floor" in combined
    assert "could not be parsed" not in combined
