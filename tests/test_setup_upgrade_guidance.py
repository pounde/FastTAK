"""setup.sh tells an upgrade to run `just upgrade`, not `./start.sh`.

On a TAK Server 5.6 → 5.8 upgrade `./start.sh` is destructive and
unrecoverable: it runs `docker compose up -d`, the tak-database image tag has
just changed, so the container is recreated — and before 5.8 the whole `cot`
database lives in that container's writable layer rather than on the volume.
The CoT history is destroyed before `just upgrade` has taken any backup of it.

setup.sh already knows which branch it took (tak/ existed or it did not), so
the closing instruction has to differ between them.

These tests run setup.sh end to end with a stub `docker` on PATH — no daemon,
no images, no network.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "setup.sh"
VERSION = "5.8-RELEASE-65"


def _fake_bundle(tmp_path: Path) -> Path:
    """A minimal hardened bundle that passes every check setup.sh makes.

    Built once per tmp_path: a test taking both fixtures runs setup.sh twice.
    """
    zip_path = tmp_path / f"takserver-docker-hardened-{VERSION}.zip"
    if zip_path.exists():
        return zip_path

    root = tmp_path / "src" / f"takserver-docker-hardened-{VERSION}"
    (root / "tak").mkdir(parents=True)
    (root / "docker").mkdir(parents=True)
    (root / "tak" / "version.txt").write_text(f"{VERSION}\n")
    (root / "docker" / "Dockerfile.hardened-takserver").write_text("FROM scratch\n")
    (root / "docker" / "Dockerfile.hardened-takserver-db").write_text("FROM scratch\n")

    subprocess.run(
        ["zip", "-q", "-r", str(zip_path), f"takserver-docker-hardened-{VERSION}"],
        cwd=tmp_path / "src",
        check=True,
    )
    return zip_path


def _stub_docker(tmp_path: Path) -> Path:
    """`docker build` succeeds and prints a plausible last line."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "Successfully tagged stub"\nexit 0\n')
    docker.chmod(0o755)
    return bin_dir


def _run_setup(tmp_path: Path, target: Path) -> subprocess.CompletedProcess:
    bin_dir = _stub_docker(tmp_path)
    return subprocess.run(
        ["/bin/bash", str(SETUP), "-d", str(target), str(_fake_bundle(tmp_path))],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        timeout=120,
    )


@pytest.fixture
def fresh(tmp_path):
    result = _run_setup(tmp_path, tmp_path / "target")
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def upgrade(tmp_path):
    """An existing deployment: tak/ and .env are already there.

    A different target directory from the fresh fixture, so a test can take
    both and compare them.
    """
    target = tmp_path / "existing"
    (target / "tak").mkdir(parents=True)
    (target / "tak" / "version.txt").write_text("5.8-RELEASE-64\n")
    env = (REPO / ".env.example").read_text()
    (target / ".env").write_text(env.replace("TAK_VERSION=", "TAK_VERSION=5.8-RELEASE-64"))
    result = _run_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def upgrade_without_tak_dir(tmp_path):
    """An existing deployment whose tak/ is gone: .env is all that is left.

    A forced clean re-extract removes tak/, and a tak/ on a mount that did not
    land is simply missing. The .env, the volumes and the containers are still
    there in both cases, so this is an upgrade — and calling it a fresh install
    hands a live pre-5.8 deployment the ./start.sh instruction that destroys its
    CoT history.
    """
    target = tmp_path / "no-tak-dir"
    target.mkdir(parents=True)
    env = (REPO / ".env.example").read_text()
    (target / ".env").write_text(env.replace("TAK_VERSION=", "TAK_VERSION=5.8-RELEASE-64"))
    result = _run_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_env_without_tak_dir_is_still_an_upgrade(upgrade_without_tak_dir):
    assert "just upgrade" in upgrade_without_tak_dir
    assert "Do NOT run ./start.sh" in upgrade_without_tak_dir


def test_fresh_install_still_says_start_sh(fresh):
    assert "./start.sh" in fresh
    assert "just upgrade" not in fresh


def test_upgrade_says_just_upgrade(upgrade):
    assert "just upgrade" in upgrade


def test_upgrade_warns_against_start_sh(upgrade):
    """The warning has to name the consequence — an operator who reads
    "run just upgrade instead" as a style preference runs ./start.sh."""
    assert "Do NOT run ./start.sh" in upgrade
    lowered = upgrade.lower()
    assert "cot history" in lowered
    assert "destroy" in lowered


def test_the_two_paths_do_not_give_the_same_instruction(fresh, upgrade):
    assert fresh != upgrade
