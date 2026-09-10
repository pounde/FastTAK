"""The justfile is a dispatcher: every recipe is one line calling a script.

`just --dry-run` prints the command a recipe would run, so the recipes can
be tested without running anything.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def dry_run(*args: str) -> str:
    result = subprocess.run(["just", "--dry-run", *args], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return (result.stderr + result.stdout).strip()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("backup", "run"), "python -m app.backup run"),
        (("backup", "run", "--actor", "lb"), "python -m app.backup run --actor lb"),
        (("backup", "list"), "python -m app.backup list"),
        (("backup", "prune", "--keep", "5"), "python -m app.backup prune --keep 5"),
    ],
)
def test_backup_passes_through_to_the_monitor_cli(args, expected):
    assert expected in dry_run(*args)


def test_old_backup_recipes_are_gone():
    listing = subprocess.run(["just", "--list"], cwd=REPO, capture_output=True, text=True).stdout
    assert "backups" not in listing
    assert "backup-prune" not in listing


def test_setup_delegates_to_setup_sh():
    assert dry_run("setup", "takserver.zip").endswith("./setup.sh takserver.zip")


def test_setup_passes_target_dir_through():
    assert "./setup.sh -d /tmp/x takserver.zip" in dry_run(
        "setup", "-d", "/tmp/x", "takserver.zip"
    )


def test_bare_just_lists_recipes():
    result = subprocess.run(["just"], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Available recipes" in result.stdout
    assert "help" not in result.stdout.split()


def test_fast_suite_delegates_to_a_script():
    assert dry_run("test").endswith("./scripts/test.sh")


@pytest.mark.parametrize("sub", ["up", "run", "down", "cycle"])
def test_test_stack_passes_the_subcommand_through(sub):
    assert dry_run("test-stack", sub).endswith(f"./tests-integration/test-stack.sh {sub}")


def test_test_stack_passes_flags_through():
    assert dry_run("test-stack", "up", "--foreground").endswith("test-stack.sh up --foreground")


def test_old_integration_recipes_are_gone():
    listing = subprocess.run(["just", "--list"], cwd=REPO, capture_output=True, text=True).stdout
    for name in ("test-integration", "test-up", "test-up-fg", "test-run", "test-down"):
        assert f" {name} " not in listing and not listing.startswith(name)


def test_check_delegates_to_the_report():
    assert dry_run("check").endswith("./scripts/check-env.sh --report")
