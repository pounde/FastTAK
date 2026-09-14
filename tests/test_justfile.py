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


def _recipe_names() -> set[str]:
    """Recipe names from `just --list`: the first token of every row that is
    not a doc line. Doc text is prose and may mention old names."""
    listing = subprocess.run(["just", "--list"], cwd=REPO, capture_output=True, text=True).stdout
    names = set()
    for row in listing.splitlines()[1:]:
        if row.strip().startswith("#"):
            continue
        names.add(row.split()[0])
    return names


def test_old_backup_recipes_are_gone():
    assert "backups" not in _recipe_names()
    assert "backup-prune" not in _recipe_names()


def test_setup_delegates_to_setup_sh():
    assert dry_run("setup", "takserver.zip").endswith("./setup.sh takserver.zip")


def test_setup_passes_target_dir_through():
    assert "./setup.sh -d /tmp/x takserver.zip" in dry_run(
        "setup", "-d", "/tmp/x", "takserver.zip"
    )


def test_bare_just_lists_recipes():
    result = subprocess.run(
        ["just", "--list", "--unsorted"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "help" not in result.stdout.split()
    lines = result.stdout.splitlines()
    for name, comments, _body, doc in _recipes():
        row = next((ln for ln in lines if ln.strip().split()[0:1] == [name]), None)
        assert row is not None, f"no --list row found for recipe {name}"
        # A multi-line [doc] renders as `#` rows above the recipe; a one-line
        # comment renders inline on the recipe's row. Either way every line
        # of the description has to reach the listing.
        description = doc.splitlines() if doc is not None else [comments[-1].lstrip("#").strip()]
        for text in description:
            assert text in result.stdout, f"recipe {name}: {text!r} not in just --list"


def test_fast_suite_delegates_to_a_script():
    assert dry_run("test").endswith("./scripts/test.sh")


@pytest.mark.parametrize("sub", ["up", "run", "down", "cycle"])
def test_test_stack_passes_the_subcommand_through(sub):
    assert dry_run("test-stack", sub).endswith(f"./tests-integration/test-stack.sh {sub}")


def test_test_stack_passes_flags_through():
    assert dry_run("test-stack", "up", "--foreground").endswith("test-stack.sh up --foreground")


def test_old_integration_recipes_are_gone():
    names = _recipe_names()
    for name in ("test-integration", "test-up", "test-up-fg", "test-run", "test-down"):
        assert name not in names


def test_check_delegates_to_the_report():
    assert dry_run("check").endswith("./scripts/check-env.sh --report")


RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+\*?[a-z]+(?:=\"\")?)*:\s*$")
DOC_OPEN = '[doc("'
DOC_CLOSE = '")]'


def _recipes() -> list[tuple[str, list[str], str, str | None]]:
    """(name, comment lines above it, first body line, [doc] text) for every public recipe.

    A `[doc("...")]` attribute may span several lines; its text is returned
    with the delimiters stripped. Any `#` lines above it (or above the header
    when there is no attribute) are the comment block.
    """
    lines = (REPO / "justfile").read_text().splitlines()
    out = []
    for i, line in enumerate(lines):
        m = RECIPE.match(line)
        if not m or m.group(1).startswith("_"):
            continue
        j = i - 1
        doc = None
        if j >= 0 and lines[j].rstrip().endswith(DOC_CLOSE):
            k = j
            while k >= 0 and not lines[k].startswith(DOC_OPEN):
                k -= 1
            assert k >= 0, f"recipe {m.group(1)}: unterminated [doc] attribute"
            text = "\n".join(lines[k : j + 1])
            doc = text[len(DOC_OPEN) : -len(DOC_CLOSE)]
            j = k - 1
        comments = []
        while j >= 0 and lines[j].startswith("#"):
            comments.insert(0, lines[j])
            j -= 1
        body = lines[i + 1].strip() if i + 1 < len(lines) else ""
        out.append((m.group(1), comments, body, doc))
    return out


def test_every_recipe_is_one_line():
    lines = (REPO / "justfile").read_text().splitlines()
    for i, line in enumerate(lines):
        if RECIPE.match(line):
            body = [ln for ln in lines[i + 1 :] if ln.strip()]
            second = body[1] if len(body) > 1 else ""
            assert not second.startswith((" ", "\t")), f"recipe {line!r} has more than one line"


def test_every_recipe_has_a_description():
    for name, comments, _body, doc in _recipes():
        assert comments or doc, f"recipe {name} has no comment block"


def test_documented_flags_exist_in_the_script():
    """The doc block is a second human-maintained copy of the script's
    arguments. This is what keeps it honest."""
    for name, comments, body, doc in _recipes():
        m = re.search(r"(\./\S+\.sh)", body)
        if not m:
            continue  # backup and the ruff recipes call something other than a local script
        script = REPO / m.group(1)
        usage = subprocess.run(
            ["/bin/bash", str(script), "--help"], capture_output=True, text=True
        ).stdout
        documented = "\n".join(comments) + "\n" + (doc or "")
        flags = set(re.findall(r"--[a-z][a-z-]*", documented))
        missing = sorted(f for f in flags if f not in usage)
        assert not missing, (
            f"recipe {name} documents {missing}, but {m.group(1)} --help does not mention them"
        )
