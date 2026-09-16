"""tak-server/start.sh, the container entrypoint FastTAK owns (distinct from
the repo-root start.sh): static checks that it stays POSIX sh and sets the
healthcheck's incident marker before TAK Server launches (#79)."""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO / "tak-server" / "start.sh"

MARKER_LINE = ": > /opt/tak/logs/incident/.tripped"


def test_entrypoint_is_posix_sh():
    result = subprocess.run(
        ["sh", "-n", str(ENTRYPOINT)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_marker_is_set_before_tak_launches():
    lines = ENTRYPOINT.read_text().splitlines()
    marker_idx = next((i for i, line in enumerate(lines) if MARKER_LINE in line), None)
    assert marker_idx is not None, f"expected the literal line {MARKER_LINE!r} in {ENTRYPOINT}"

    launch_idx = next(
        (i for i, line in enumerate(lines) if "configureInDocker" in line and "exec" in line),
        None,
    )
    assert launch_idx is not None, "could not find the line that execs TAK Server"

    assert marker_idx < launch_idx, (
        "the incident marker must be set before TAK Server is launched, "
        f"got marker at line {marker_idx + 1}, launch at line {launch_idx + 1}"
    )
