# tests/test_upgrade_planning.py
"""Version-gap detection in scripts/upgrade.sh.

Only the pure planning helpers are unit-tested — the migration itself moves
Docker volumes and is covered by tests-integration/test_upgrade_rehearsal.py.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UPGRADE = REPO / "scripts" / "upgrade.sh"


def _call(func: str, *args: str) -> subprocess.CompletedProcess:
    """Source upgrade.sh with FASTAK_UPGRADE_LIB_ONLY=1 and call one function.

    `bash -c SCRIPT NAME ARG1 ARG2` binds NAME to $0, so the first real
    argument is $1 — not $2.
    """
    quoted = " ".join(f'"${i + 1}"' for i in range(len(args)))
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'FASTAK_UPGRADE_LIB_ONLY=1 . "{UPGRADE}"; {func} {quoted}',
            "_",
            *args,
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("current,target", [("15", "18"), ("17", "18"), ("18", "19")])
def test_differing_majors_need_migration(current, target):
    assert _call("upgrade_needs_migration", current, target).returncode == 0


@pytest.mark.parametrize("major", ["15", "18"])
def test_same_major_needs_no_migration(major):
    assert _call("upgrade_needs_migration", major, major).returncode == 1


def test_absent_current_major_needs_no_migration():
    """No volume means a fresh install — nothing to migrate."""
    assert _call("upgrade_needs_migration", "", "18").returncode == 1


def test_volume_major_of_missing_volume_is_empty():
    result = _call("upgrade_volume_pg_major", "fastak-definitely-no-such-volume")
    assert result.stdout.strip() == ""
