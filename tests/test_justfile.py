"""The justfile is a dispatcher: every recipe is one line calling a script.

`just --dry-run` prints the command a recipe would run, so the recipes can
be tested without running anything.
"""

import re
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


RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+\*?[a-z]+(?:=\"\")?)*:\s*$")


def _recipes() -> list[tuple[str, list[str], str]]:
    """(name, comment lines above it, first body line) for every public recipe."""
    lines = (REPO / "justfile").read_text().splitlines()
    out = []
    for i, line in enumerate(lines):
        m = RECIPE.match(line)
        if not m or m.group(1).startswith("_"):
            continue
        comments = []
        j = i - 1
        while j >= 0 and lines[j].startswith("#"):
            comments.insert(0, lines[j])
            j -= 1
        body = lines[i + 1].strip() if i + 1 < len(lines) else ""
        out.append((m.group(1), comments, body))
    return out


def test_every_recipe_is_one_line():
    lines = (REPO / "justfile").read_text().splitlines()
    for i, line in enumerate(lines):
        if RECIPE.match(line):
            body = [ln for ln in lines[i + 1 :] if ln.strip()]
            second = body[1] if len(body) > 1 else ""
            assert not second.startswith((" ", "\t")), f"recipe {line!r} has more than one line"


def test_every_recipe_has_a_description():
    for name, comments, _ in _recipes():
        assert comments, f"recipe {name} has no comment block"


def test_documented_flags_exist_in_the_script():
    """The comment block is a second human-maintained copy of the script's
    arguments. This is what keeps it honest."""
    for name, comments, body in _recipes():
        m = re.search(r"(\./\S+\.sh)", body)
        if not m:
            continue  # backup and the ruff recipes call something other than a local script
        script = REPO / m.group(1)
        usage = subprocess.run(
            ["/bin/bash", str(script), "--help"], capture_output=True, text=True
        ).stdout
        flags = set(re.findall(r"--[a-z][a-z-]*", "\n".join(comments)))
        missing = sorted(f for f in flags if f not in usage)
        assert not missing, (
            f"recipe {name} documents {missing}, but {m.group(1)} --help does not mention them"
        )
