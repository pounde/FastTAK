"""setup.sh tells an upgrade to clear the volumes first, not just `./start.sh`.

Crossing the TAK Server 5.8 boundary changes the PostgreSQL major under both
databases, so the old volumes cannot be started on the new images. The upgrade
route is `just backup` → `docker compose down -v` → `./start.sh`: the databases
come back empty, while the CA, the issued client certs and CoreConfig.xml live
on the host under tak/ and are preserved. An operator handed the plain fresh
install instruction would start onto volumes the new server refuses.

setup.sh already knows which branch it took (tak/ or .env existed, or neither),
so the closing instruction has to differ between them.

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
    hands a live deployment a bare ./start.sh onto stale volumes.
    """
    target = tmp_path / "no-tak-dir"
    target.mkdir(parents=True)
    env = (REPO / ".env.example").read_text()
    (target / ".env").write_text(env.replace("TAK_VERSION=", "TAK_VERSION=5.8-RELEASE-64"))
    result = _run_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_env_without_tak_dir_is_still_an_upgrade(upgrade_without_tak_dir):
    assert "docker compose down -v" in upgrade_without_tak_dir


def test_fresh_install_just_says_start_sh(fresh):
    assert "./start.sh" in fresh
    assert "docker compose down -v" not in fresh


def test_upgrade_gives_the_whole_sequence_in_order(upgrade):
    """Backup, then drop the volumes, then start — in that order. Dropping the
    volumes before the backup leaves nothing to have backed up."""
    backup = upgrade.index("just backup")
    down = upgrade.index("docker compose down -v")
    start = upgrade.index("./start.sh")
    assert backup < down < start


def test_upgrade_says_the_databases_are_not_carried_across(upgrade):
    """An operator who reads `down -v` as routine tidying loses data they
    thought was migrating. Name the consequence."""
    lowered = upgrade.lower()
    assert "cot history is not carried across" in lowered
    assert "empty" in lowered


def test_upgrade_says_the_certificates_survive(upgrade):
    """The other half of the split, and the half that decides whether the
    operator goes through with it."""
    lowered = upgrade.lower()
    assert "certificates" in lowered
    assert "preserved" in lowered


def test_upgrade_points_at_the_full_procedure(upgrade):
    assert "docs/upgrading.md" in upgrade


def test_the_two_paths_do_not_give_the_same_instruction(fresh, upgrade):
    assert fresh != upgrade
