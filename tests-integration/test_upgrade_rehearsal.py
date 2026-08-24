"""Upgrade rehearsal: run `just upgrade` against a stack and assert data survives.

Marked `slow` — each case tears the stack down and brings it back up twice.

The rehearsal seeds a small, identifiable row in each app-db database, runs
the upgrade, and checks the row is still there. It cannot say anything about
behaviour at realistic cot sizes; that is tracked in #98.

Two pieces of setup exist here purely to make `upgrade.sh` actually take its
destructive path, rather than exit at "Nothing to migrate":

1. `_force_app_db_legacy` recreates the running app-db container on
   postgres:15-alpine against a fresh volume before seeding. docker-compose.yml
   on this branch already pins app-db to postgres:18-alpine, so a stack
   brought up by `just test-up` starts with app-db (and, once TAK 5.8 has run
   initdb, tak-database) already on the target major. Run `upgrade.sh`
   against that unmodified and its own planning step reports "app-db:
   PostgreSQL 18 -> 18" / "tak-database: PostgreSQL 18 -> 18" and exits
   before taking a backup, stopping the stack, or touching a single volume —
   confirmed by hand before this helper existed. That would make every
   assertion in this file trivially true without the restore ever running,
   which is exactly the "cannot distinguish restored from recreated empty"
   failure this test exists to rule out. Downgrading app-db mirrors the real
   operator sequence: `git pull` lands the new pin, but the *running*
   container is still the old image until something recreates it, so the
   backup step still reaches a live PG15 app-db exactly as it would in
   production. Once app-db's major genuinely differs, upgrade.sh's own
   planning always removes and restores tak-db-data too (regardless of its
   own major — see upgrade_cot_plan in scripts/upgrade.sh), so this one
   downgrade is enough to exercise both restores.

2. `_ensure_backup_dir_in_env` writes an absolute BACKUP_DIR into the test
   stack's own .env file. upgrade.sh reads BACKUP_DIR only from that file
   (never from the process environment — see its own comment on
   BACKUP_DIR_RESOLVED), so without this it resolves to <repo>/backups on
   the host while the monitor container's /backups mount (fixed at
   container-creation time by test-setup.sh) points at the test's isolated
   backups dir. The backup itself still succeeds — monitor writes it to the
   right place — but upgrade.sh then can't find the archive it just watched
   monitor produce, and aborts (non-destructively) with "not at $ARCHIVE".
   Confirmed by hand before this helper existed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.destructive]

REPO = Path(__file__).resolve().parents[1]
SENTINEL = "fastak_upgrade_rehearsal"

APP_DB_LEGACY_OVERRIDE = "services:\n  app-db:\n    image: postgres:15-alpine\n"


def _state_dir() -> Path:
    matches = list(Path("/tmp").glob("fastak-test-*/.test-state"))
    assert matches, "no /tmp/fastak-test-*/.test-state — is `just test-up` running?"
    return max(matches, key=lambda p: p.stat().st_mtime).parent


def _state(key: str) -> str:
    for line in (_state_dir() / ".test-state").read_text().splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError(f"{key} not found in .test-state")


def _compose_env() -> dict[str, str]:
    """Environment docker compose needs to interpolate docker-compose.test.yml.
    Mirrors conftest.py's compose_env fixture — this file can't use it
    directly since these commands run outside any fixture-driven test body.
    """
    env_file = _state("ENV_FILE")
    return {
        **os.environ,
        "TAK_HOST_PATH": _state("TAK_HOST_PATH"),
        "BACKUP_DIR": f"{_state('TEST_DIR')}/backups",
        "HOST_ENV_FILE": env_file,
    }


def _compose_base_args(project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(REPO / "docker-compose.yml"),
        "-f",
        str(REPO / "docker-compose.test.yml"),
        "--env-file",
        _state("ENV_FILE"),
    ]


def _app_db_sql(project: str, database: str, sql: str) -> str:
    shell_cmd = (
        f'PGPASSWORD="$POSTGRES_PASSWORD" psql -h localhost -U fastak -d {database} -tAc "{sql}"'
    )
    result = subprocess.run(
        ["docker", "compose", "-p", project, "exec", "-T", "app-db", "sh", "-c", shell_cmd],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _cot_sql(project: str, sql: str) -> str:
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
            f'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot -tAc "{sql}"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _seed(project: str) -> None:
    """Write a sentinel row into each app-db database and into cot."""
    for database in ("lldap", "nodered", "fastak"):
        _app_db_sql(
            project,
            database,
            f"CREATE TABLE IF NOT EXISTS {SENTINEL} (id int primary key); "
            f"INSERT INTO {SENTINEL} VALUES (1) ON CONFLICT DO NOTHING;",
        )
    _cot_sql(
        project,
        f"CREATE TABLE IF NOT EXISTS {SENTINEL} (id int primary key); "
        f"INSERT INTO {SENTINEL} VALUES (1) ON CONFLICT DO NOTHING;",
    )


def _sentinel_rows(project: str, database: str) -> int:
    exists = _app_db_sql(
        project,
        database,
        f"SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema='public' AND table_name='{SENTINEL}'",
    )
    if exists != "1":
        return 0
    return int(_app_db_sql(project, database, f"SELECT count(*) FROM {SENTINEL}"))


def _cot_sentinel_rows(project: str) -> int:
    exists = _cot_sql(
        project,
        f"SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema='public' AND table_name='{SENTINEL}'",
    )
    if exists != "1":
        return 0
    return int(_cot_sql(project, f"SELECT count(*) FROM {SENTINEL}"))


def _ensure_backup_dir_in_env() -> None:
    """See module docstring, point 2. Idempotent — safe to call per test."""
    env_file = Path(_state("ENV_FILE"))
    target = f"{_state('TEST_DIR')}/backups"
    lines = env_file.read_text().splitlines()
    out = []
    found = False
    for line in lines:
        bare = line.strip().lstrip("#").strip()
        if bare.startswith("BACKUP_DIR="):
            out.append(f"BACKUP_DIR={target}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"BACKUP_DIR={target}")
    env_file.write_text("\n".join(out) + "\n")


def _app_db_volume(project: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.volume=app-db-data",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    names = [n for n in result.stdout.splitlines() if n.strip()]
    return names[0] if names else f"{project}_app-db-data"


def _wait_service_healthy(project: str, service: str, timeout: int = 90) -> None:
    """Poll `docker inspect`'s Health.Status for `service` until healthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cid_out = subprocess.run(
            _compose_base_args(project) + ["ps", "-q", service],
            capture_output=True,
            text=True,
            env=_compose_env(),
        ).stdout.strip()
        if cid_out:
            status = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    cid_out.splitlines()[0],
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if status == "healthy":
                return
        time.sleep(3)
    raise AssertionError(f"{service} did not become healthy within {timeout}s")


def _wait_app_db_healthy(project: str, timeout: int = 90) -> None:
    _wait_service_healthy(project, "app-db", timeout)


def _wait_fastak_schema(project: str, timeout: int = 60) -> None:
    """Wait for the monitor to (re-)create the fastak_events table.

    monitor/app/main.py's FastAPI lifespan calls app.audit.init_schema()
    once at process startup, which issues the `CREATE TABLE IF NOT EXISTS
    fastak_events (...)` in monitor/app/audit.py. Restarting the monitor
    container re-runs that startup path against the fresh, schema-less
    app-db. Polling for the table itself (rather than sleeping a fixed
    amount, or hand-creating the table here) tracks whatever that startup
    path actually does, so it won't drift if init_schema's timing or
    contents change.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _app_db_sql(
            project,
            "fastak",
            "SELECT to_regclass('public.fastak_events') IS NOT NULL",
        )
        if last == "t":
            return
        time.sleep(2)
    raise AssertionError(
        f"fastak_events did not appear in app-db's fastak database within "
        f"{timeout}s after restarting monitor (last check returned {last!r})"
    )


def _force_app_db_legacy(project: str) -> None:
    """Recreate app-db on postgres:15-alpine against a fresh volume. See the
    module docstring, point 1, for why this is necessary.

    A fresh volume means app-db/start.sh creates the lldap/nodered/fastak
    databases (see that script) but none of the schema inside them, notably
    no fastak_events table. upgrade.sh's backup step needs fastak_events to
    exist (it audits via app.audit.record_event) or it fails before doing
    anything destructive — correctly, but before this test gets to exercise
    anything. Rather than hand-create that schema here (drifting the moment
    audit.py changes), restart the monitor so it recreates its own schema
    against the legacy app-db (monitor/app/main.py's FastAPI lifespan calls
    app.audit.init_schema() on every startup), then wait on that specific
    table rather than a fixed sleep.

    lldap is deliberately left alone: restarting it hits an unrelated,
    pre-existing quirk in the lldap/lldap image — it derives its signing
    key from a key_seed on first boot but then refuses to start against an
    existing server_key file on any later boot ("A key_seed was given, but
    a key file already exists... aborting"), regardless of app-db. That's
    orthogonal to this test and out of scope to fix here (would mean
    touching docker-compose.yml or the lldap image config). It's also
    unnecessary for what this test checks: upgrade.sh's backup/restore of
    the lldap database is a plain pg_dump/restore that doesn't care whether
    LLDAP's own tables exist, and the test's own _seed() creates its
    sentinel table directly.
    """
    env = _compose_env()

    remove = subprocess.run(
        _compose_base_args(project) + ["rm", "--stop", "--force", "app-db"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert remove.returncode == 0, (
        f"could not stop/remove app-db:\n{remove.stdout}\n{remove.stderr}"
    )

    volume = _app_db_volume(project)
    vol_rm = subprocess.run(["docker", "volume", "rm", volume], capture_output=True, text=True)
    assert vol_rm.returncode == 0, f"could not remove {volume}:\n{vol_rm.stdout}\n{vol_rm.stderr}"

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(APP_DB_LEGACY_OVERRIDE)
        override_path = f.name
    try:
        up = subprocess.run(
            _compose_base_args(project) + ["-f", override_path, "up", "-d", "app-db"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert up.returncode == 0, f"could not bring up legacy app-db:\n{up.stdout}\n{up.stderr}"
    finally:
        os.unlink(override_path)

    _wait_app_db_healthy(project)

    version = _app_db_sql(project, "postgres", "SHOW server_version_num;")
    assert version.startswith("15"), (
        f"expected app-db on PostgreSQL 15, server_version_num={version}"
    )

    # monitor was connected to the old app-db; restart it so its FastAPI
    # lifespan re-runs init_schema() against the fresh one. See the
    # docstring above for why lldap is not restarted here too.
    restart = subprocess.run(
        _compose_base_args(project) + ["restart", "monitor"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert restart.returncode == 0, (
        f"could not restart monitor after downgrading app-db:\n{restart.stdout}\n{restart.stderr}"
    )

    _wait_fastak_schema(project)


def _run_upgrade(project: str, *flags: str) -> subprocess.CompletedProcess:
    """Run scripts/upgrade.sh against the isolated test stack.

    FASTAK_ENV_FILE and FASTAK_COMPOSE_FILES point the script at the test
    stack's .env and its port-remapping overlay. Without them upgrade.sh
    resolves the repo's own .env and omits docker-compose.test.yml, which
    would bring the stack up on production port bindings.
    """
    env_file = _state("ENV_FILE")
    return subprocess.run(
        ["/bin/bash", str(REPO / "scripts" / "upgrade.sh"), "--yes", *flags],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "COMPOSE_PROJECT_NAME": project,
            "FASTAK_ENV_FILE": env_file,
            "FASTAK_COMPOSE_FILES": f"{REPO}/docker-compose.yml:{REPO}/docker-compose.test.yml",
            "TAK_HOST_PATH": _state("TAK_HOST_PATH"),
            "BACKUP_DIR": f"{_state('TEST_DIR')}/backups",
            "HOST_ENV_FILE": env_file,
        },
    )


def test_upgrade_preserves_app_db_data():
    """The default path: every app-db database survives the major bump."""
    project = _state("PROJECT")
    _ensure_backup_dir_in_env()
    _force_app_db_legacy(project)
    _seed(project)
    for database in ("lldap", "nodered", "fastak"):
        assert _sentinel_rows(project, database) == 1, f"seed failed for {database}"

    result = _run_upgrade(project)
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"

    for database in ("lldap", "nodered", "fastak"):
        assert _sentinel_rows(project, database) == 1, (
            f"{database} lost its sentinel row across the upgrade"
        )


def test_upgrade_preserves_cot_by_default():
    """CoT history is migrated unless --skip-cot is passed. This is the
    standing behaviour for every future upgrade."""
    project = _state("PROJECT")
    _ensure_backup_dir_in_env()
    _force_app_db_legacy(project)
    _seed(project)
    assert _cot_sentinel_rows(project) == 1, "seed failed for cot"

    result = _run_upgrade(project)
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert _cot_sentinel_rows(project) == 1, "cot lost its sentinel row"


def test_skip_cot_discards_cot_but_keeps_app_db():
    """--skip-cot is the one-time choice for the 5.6 → 5.8 hop."""
    project = _state("PROJECT")
    _ensure_backup_dir_in_env()
    _force_app_db_legacy(project)
    _seed(project)

    result = _run_upgrade(project, "--skip-cot")
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"

    assert _cot_sentinel_rows(project) == 0, "--skip-cot should have discarded cot"
    for database in ("lldap", "nodered", "fastak"):
        assert _sentinel_rows(project, database) == 1, f"--skip-cot must not affect {database}"


def test_unknown_flag_is_rejected_before_anything_destructive():
    """Argument parsing runs before the backup, the down, or any volume rm.

    cwd is irrelevant — upgrade.sh cd's to its own repo root — so this asserts
    on the parse, which is the only thing that can be checked without a stack.
    """
    result = subprocess.run(
        ["/bin/bash", str(REPO / "scripts" / "upgrade.sh"), "--not-a-real-flag"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unknown argument" in result.stderr
