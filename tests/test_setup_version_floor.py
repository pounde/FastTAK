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


def test_rejects_unparseable_version(tmp_path):
    zip_path = _fake_bundle(tmp_path, "5.6-RELEASE-6")
    # Overwrite version.txt inside the zip with garbage.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    subprocess.run(["unzip", "-q", str(zip_path), "-d", str(scratch)], check=True)
    vt = scratch / "takserver-docker-hardened-5.6-RELEASE-6" / "tak" / "version.txt"
    vt.write_text("not-a-version\n")
    zip_path.unlink()
    subprocess.run(
        ["zip", "-q", "-r", str(zip_path), "takserver-docker-hardened-5.6-RELEASE-6"],
        cwd=scratch,
        check=True,
    )
    result = _run_setup(zip_path, tmp_path / "target")
    assert result.returncode != 0
