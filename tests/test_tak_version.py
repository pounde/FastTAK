"""TAK Server version parsing and floor comparison.

Covers scripts/lib-tak-version.sh, which setup.sh and check-env.sh both
source. The two callers must agree on what "5.8 or later" means — a version
one accepts and the other rejects is how a deployment ends up half-upgraded.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib-tak-version.sh"


def _major_minor(version: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", f'. "{LIB}"; tak_version_major_minor "$1"', "_", version],
        capture_output=True,
        text=True,
    )


def _meets_floor(version: str, floor: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", f'. "{LIB}"; tak_version_meets_floor "$1" "$2"', "_", version, floor],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "version,expected",
    [
        ("5.8-RELEASE-65", "5.8"),
        ("5.6-RELEASE-6", "5.6"),
        ("5.10-RELEASE-1", "5.10"),
        ("6.0-RELEASE-1", "6.0"),
        ("5.8", "5.8"),
    ],
)
def test_major_minor_parses(version, expected):
    result = _major_minor(version)
    assert result.returncode == 0
    assert result.stdout == expected


@pytest.mark.parametrize("version", ["", "RELEASE-65", "abc", "5", "v5.8", "5.x-RELEASE-1"])
def test_major_minor_rejects_unparseable(version):
    result = _major_minor(version)
    assert result.returncode == 1
    assert result.stdout == ""


@pytest.mark.parametrize(
    "version,floor",
    [
        ("5.8-RELEASE-65", "5.8"),
        ("5.9-RELEASE-1", "5.8"),
        ("5.10-RELEASE-1", "5.8"),  # minor compared numerically, not as a string
        ("6.0-RELEASE-1", "5.8"),
        ("5.8", "5.8"),
    ],
)
def test_meets_floor_accepts(version, floor):
    assert _meets_floor(version, floor).returncode == 0


@pytest.mark.parametrize(
    "version,floor",
    [
        ("5.6-RELEASE-6", "5.8"),
        ("5.7-RELEASE-1", "5.8"),
        ("4.9-RELEASE-1", "5.8"),
        ("", "5.8"),
        ("garbage", "5.8"),
    ],
)
def test_meets_floor_rejects(version, floor):
    assert _meets_floor(version, floor).returncode == 1
