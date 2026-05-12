"""End-to-end backup → wipe → restore → validate.

Marked `@pytest.mark.integration` and `@pytest.mark.slow` — the full
stack tear-down + bring-up + bring-up cycle takes ~2 minutes. Run via
`just test-up && just test-run`. Opt out with `-m 'not slow'`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _project_state_dir() -> Path:
    # `test-setup.sh` writes the project name to /tmp/fastak-test-*/.test-state
    matches = list(Path("/tmp").glob("fastak-test-*/.test-state"))
    assert matches, "no /tmp/fastak-test-*/.test-state — is `just test-up` running?"
    # Newest by mtime (in case a previous run left stale dirs behind).
    return max(matches, key=lambda p: p.stat().st_mtime).parent


def _project_name(state_dir: Path) -> str:
    """Parse PROJECT=... from `.test-state` (shell-style key=val, one per line)."""
    for line in (state_dir / ".test-state").read_text().splitlines():
        line = line.strip()
        if line.startswith("PROJECT="):
            value = line.split("=", 1)[1].strip()
            return value.strip('"').strip("'")
    raise AssertionError(f"PROJECT not found in {state_dir / '.test-state'}")


def _cot_table_count(project: str) -> int:
    """Count public-schema tables in the `cot` DB.

    TAK Server's cot DB initializes with 100+ tables. After a restore that
    silently failed to repopulate, the DB would have only what compose's
    fresh init created (~0). This is a coarse but reliable signal that the
    cot.sql dump actually got piped in.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "exec",
            "-T",
            "tak-database",
            "sh",
            "-c",
            'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot '
            '-tA -c "SELECT count(*) FROM information_schema.tables '
            "WHERE table_schema = 'public'\"",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def test_backup_then_restore_round_trip(tmp_path):
    state_dir = _project_state_dir()
    project = _project_name(state_dir)
    repo_dir = Path(__file__).resolve().parent.parent

    # 1. Seed: create an LLDAP user, confirm it's present.
    # Schema has: user_id, email, creation_date, uuid (NOT NULL); lowercase_email
    # defaults to 'UNSET' but we set it explicitly so the dump round-trips meaningfully.
    _run(
        "docker",
        "compose",
        "-p",
        project,
        "exec",
        "-T",
        "app-db",
        "psql",
        "-U",
        "fastak",
        "-d",
        "lldap",
        "-c",
        "INSERT INTO users (user_id, email, lowercase_email, display_name, "
        "creation_date, uuid) "
        "VALUES ('bktest', 'bk@test', 'bk@test', 'bk test', NOW(), "
        "'00000000-0000-4000-8000-bktestbktest');",
    )

    # Capture the cot DB's table count so we can verify TAK Server's full
    # schema (100+ tables) round-trips, not just the seeded LLDAP row.
    cot_tables_pre = _cot_table_count(project)
    assert cot_tables_pre > 50, (
        f"cot DB has only {cot_tables_pre} tables pre-backup — stack not fully initialized"
    )

    # 2. Run a backup via the monitor CLI.
    _run(
        "docker",
        "compose",
        "-p",
        project,
        "exec",
        "-T",
        "monitor",
        "python",
        "-m",
        "app.backup",
        "run",
    )

    # 3. Locate the produced tarball + key (they live in BACKUP_DIR on the host,
    #    which test-setup.sh sets under state_dir).
    backups_dir = state_dir / "backups"
    tarballs = list(backups_dir.glob("fasttak-backup-*.age"))
    assert len(tarballs) == 1, f"expected one tarball, got {tarballs}"
    keyfile = backups_dir / ".age-identity"
    assert keyfile.exists()

    # 4. Copy artifacts out so the wipe doesn't take them.
    keep_dir = tmp_path / "keep"
    keep_dir.mkdir()
    backup_copy = keep_dir / tarballs[0].name
    key_copy = keep_dir / "key.txt"
    backup_copy.write_bytes(tarballs[0].read_bytes())
    key_copy.write_text(keyfile.read_text())

    # 5. Tear down the old stack. test-down -v removes the named volumes and
    #    nukes /tmp/<project>/, so there's nothing left to clean up by hand.
    _run("just", "test-down")

    # 6. Scaffold a fresh test stack WITHOUT booting containers. setup.sh
    #    extracts a fresh tak/ and writes a fresh .env; restore.sh will
    #    overwrite that .env before any container starts. This mirrors the
    #    canonical "fresh host + setup.sh + restore" flow in
    #    docs/backup-and-restore.md.
    _run("bash", str(repo_dir / "tests-integration" / "test-setup.sh"), "--no-up")
    fresh_state_dir = _project_state_dir()
    fresh_project = _project_name(fresh_state_dir)
    assert fresh_project != project, "expected a fresh test project after teardown+setup"

    # 7. Run the canonical restore procedure. It replaces .env, restores
    #    certs + the Node-RED volume, brings DB services up (so they init
    #    with the restored secrets), restores the application DBs, and
    #    finally brings up the rest of the stack.
    _run(
        "bash",
        str(repo_dir / "tests-integration" / "restore.sh"),
        fresh_project,
        str(backup_copy),
        str(key_copy),
        str(fresh_state_dir / ".env"),
        str(fresh_state_dir / "tak"),
        str(repo_dir),
        str(repo_dir / "docker-compose.test.yml"),
    )

    try:
        # 8a. Validate the seeded LLDAP user is back.
        result = _run(
            "docker",
            "compose",
            "-p",
            fresh_project,
            "exec",
            "-T",
            "app-db",
            "psql",
            "-U",
            "fastak",
            "-d",
            "lldap",
            "-tA",
            "-c",
            "SELECT user_id FROM users WHERE user_id = 'bktest'",
        )
        assert "bktest" in result.stdout

        # 8b. Validate the cot DB schema round-tripped. A silent restore
        #     failure (e.g. cot.sql piped into the wrong DB, or psql peer-auth
        #     fell through) would leave the cot DB at compose's fresh-init
        #     table count; the assert below catches that.
        cot_tables_post = _cot_table_count(fresh_project)
        assert cot_tables_post == cot_tables_pre, (
            f"cot DB has {cot_tables_post} tables post-restore, expected {cot_tables_pre}"
        )
    finally:
        # 9. Clean up the seed row so subsequent tests see a bootstrap-only
        #    LLDAP state (test_group_enforcement.TestBootstrapState expects
        #    {webadmin}). Runs even on assertion failure so a flaky run
        #    doesn't cascade into later tests.
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                fresh_project,
                "exec",
                "-T",
                "app-db",
                "psql",
                "-U",
                "fastak",
                "-d",
                "lldap",
                "-c",
                "DELETE FROM users WHERE user_id = 'bktest';",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
