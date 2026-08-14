# tests/test_pgdata_guard.py
"""PGDATA must be on a mount, not the container's writable layer.

The original defect: tak-db-data was mounted at /var/lib/postgresql/data
while TAK 5.6 ran initdb into /var/lib/postgresql/15/data. The cot database
lived in the container layer and `docker compose down` destroyed it.

The guard takes the mounts file as an argument so it can be tested against
fixtures instead of requiring a container.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "tak-database" / "check-pgdata-persistent.sh"

MOUNTED = """\
overlay / overlay rw,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec 0 0
/dev/vda1 /var/lib/postgresql/data ext4 rw,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev 0 0
"""

NOT_MOUNTED = """\
overlay / overlay rw,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev 0 0
"""

# The exact 5.6 shape: the volume is mounted, but one directory up from
# where Postgres actually writes.
WRONG_PATH_MOUNTED = """\
overlay / overlay rw,relatime 0 0
/dev/vda1 /var/lib/postgresql/data ext4 rw,relatime 0 0
"""

# A genuine string prefix of the queried data dir, but a different directory
# ("dat" vs "data") — not a shared-path-segment near-miss like the 5.6 shape.
PREFIX_OF_DATA_DIR_MOUNTED = """\
overlay / overlay rw,relatime 0 0
/dev/vda1 /var/lib/postgresql/dat ext4 rw,relatime 0 0
"""

# The reverse: the queried data dir is a string prefix of the mount point.
DATA_DIR_IS_PREFIX_OF_MOUNT = """\
overlay / overlay rw,relatime 0 0
/dev/vda1 /var/lib/postgresql/database ext4 rw,relatime 0 0
"""

# A volume mounted at the parent of PGDATA. This genuinely would persist
# PGDATA on a real filesystem, but the guard requires an exact match anyway.
PARENT_DIR_MOUNTED = """\
overlay / overlay rw,relatime 0 0
/dev/vda1 /var/lib/postgresql ext4 rw,relatime 0 0
"""


def _run(data_dir: str, mounts: str, tmp_path: Path) -> subprocess.CompletedProcess:
    mounts_file = tmp_path / "mounts"
    mounts_file.write_text(mounts)
    return subprocess.run(
        ["/bin/bash", str(GUARD), data_dir, str(mounts_file)],
        capture_output=True,
        text=True,
    )


def test_passes_when_data_dir_is_a_mount(tmp_path):
    result = _run("/var/lib/postgresql/data", MOUNTED, tmp_path)
    assert result.returncode == 0, result.stderr


def test_fails_when_data_dir_is_container_layer(tmp_path):
    result = _run("/var/lib/postgresql/data", NOT_MOUNTED, tmp_path)
    assert result.returncode == 1
    assert "/var/lib/postgresql/data" in result.stderr


def test_fails_on_the_5_6_shape(tmp_path):
    """Volume mounted at .../data, Postgres writing to .../15/data."""
    result = _run("/var/lib/postgresql/15/data", WRONG_PATH_MOUNTED, tmp_path)
    assert result.returncode == 1
    assert "15/data" in result.stderr


def test_fails_on_a_true_prefix_near_miss(tmp_path):
    """Mount /var/lib/postgresql/dat is a string prefix of the queried
    /var/lib/postgresql/data but names a different directory. A guard
    implemented as a prefix match (e.g. `index(d, $2) == 1`) would accept
    this; the guard must reject it, since PGDATA is not actually that mount.
    """
    result = _run("/var/lib/postgresql/data", PREFIX_OF_DATA_DIR_MOUNTED, tmp_path)
    assert result.returncode == 1
    assert "/var/lib/postgresql/data" in result.stderr


def test_fails_on_the_reverse_prefix_near_miss(tmp_path):
    """The queried /var/lib/postgresql/data is itself a string prefix of the
    mount point /var/lib/postgresql/database. A containment check written in
    the other direction (e.g. `index($2, d) == 1`) would accept this; the
    guard must reject it.
    """
    result = _run("/var/lib/postgresql/data", DATA_DIR_IS_PREFIX_OF_MOUNT, tmp_path)
    assert result.returncode == 1
    assert "/var/lib/postgresql/data" in result.stderr


def test_fails_on_a_parent_directory_mount(tmp_path):
    """A volume mounted at /var/lib/postgresql — the parent of PGDATA —
    would, on a real filesystem, genuinely persist /var/lib/postgresql/data:
    anything written under a mounted directory lives on that mount. A
    strictly-correct "is this path backed by a mounted filesystem" check
    would accept it.

    This guard deliberately does not. It requires PGDATA to be exactly a
    mount point, matching what FastTAK's docker-compose.yml configures
    (the tak-db-data volume is mounted directly at PGDATA, not at its
    parent). The guard is biased toward false alarms over false passes: a
    false alarm here is noticed immediately when the container fails to
    start, whereas a false pass would silently reintroduce the class of bug
    this script exists to catch (a compose file that changes PGDATA's
    parent directory without moving the volume mount to match). Do not
    "fix" this into acceptance without weighing that tradeoff.
    """
    result = _run("/var/lib/postgresql/data", PARENT_DIR_MOUNTED, tmp_path)
    assert result.returncode == 1
    assert "/var/lib/postgresql/data" in result.stderr


def test_trailing_slash_is_tolerated(tmp_path):
    result = _run("/var/lib/postgresql/data/", MOUNTED, tmp_path)
    assert result.returncode == 0, result.stderr


def test_missing_mounts_file_fails(tmp_path):
    result = subprocess.run(
        ["/bin/bash", str(GUARD), "/var/lib/postgresql/data", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_missing_argument_fails(tmp_path):
    result = subprocess.run(["/bin/bash", str(GUARD)], capture_output=True, text=True)
    assert result.returncode != 0
