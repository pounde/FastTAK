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
