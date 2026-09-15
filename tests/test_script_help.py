"""Every argument-taking script answers --help.

The justfile is the index and the scripts are the implementation; the
handoff between them has to land somewhere better than reading source.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

SCRIPTS = [
    "start.sh",
    "setup.sh",
    "scripts/down.sh",
    "scripts/check-env.sh",
    "scripts/env-get.sh",
    "scripts/test.sh",
    "tests-integration/test-stack.sh",
]

# Scripts with no meaningful zero-argument behaviour print usage when bare.
BARE_IS_USAGE = ["setup.sh", "scripts/env-get.sh", "tests-integration/test-stack.sh"]


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(REPO / script), *args], capture_output=True, text=True, timeout=30
    )


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_and_exits_zero(script, flag):
    result = _run(script, flag)
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()
    assert result.stderr == ""


@pytest.mark.parametrize("script", BARE_IS_USAGE)
def test_bare_invocation_prints_usage_and_fails(script):
    result = _run(script)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_start_help_documents_every_flag():
    out = _run("start.sh", "--help").stdout
    for flag in ("--capture", "--checks", "--no-checks", "--no-wait", "--verbose"):
        assert flag in out


def test_test_stack_rejects_an_unknown_subcommand():
    result = _run("tests-integration/test-stack.sh", "bogus")
    assert result.returncode == 2
    assert "bogus" in result.stderr


def test_test_stack_down_rejects_a_non_project_name():
    result = _run("tests-integration/test-stack.sh", "down", "../x")
    assert result.returncode == 2
    assert "../x" in result.stderr


def test_test_stack_down_accepts_a_well_formed_project_name():
    result = _run("tests-integration/test-stack.sh", "down", "fastak-test-123")
    assert result.returncode == 0, result.stderr
