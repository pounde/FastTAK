"""Orchestrates a single backup run.

Heavy lifters (`_dump_postgres`, `_tar_certs`, `_tar_tak_config`,
`_tar_nodered_data`, `_copy_env`, `_stream_encrypted_archive`) are
module-level functions so unit tests can monkeypatch them. The
integration test exercises them for real.

Lock semantics: an exclusive flock on `<BACKUP_DIR>/.backup.lock` held
for the lifetime of `run()`. Acquired non-blocking; if held by another
process, raise `BackupAlreadyRunning`.

State transitions: write `running` before doing any work, then either
`success` (filename + size + finished_at) or `failed` (error + finished_at).

Audit: emit `backup.started` immediately after acquiring the lock,
`backup.completed` or `backup.failed` before releasing it. Retention
runs only on success and emits `backup.pruned` per deleted file.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.audit import record_event
from app.backup import state
from app.backup.config import (
    backup_dir,
    fasttak_version,
    retention_keep,
)
from app.backup.exceptions import BackupAlreadyRunning
from app.backup.keys import load_or_create
from app.backup.manifest import build as build_manifest
from app.backup.manifest import collect_postgres_versions as _collect_postgres_versions
from app.backup.retention import prune
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class BackupResult:
    filename: str
    size_bytes: int


def _now() -> datetime:
    return datetime.now(UTC)


def _filename(created_at: datetime) -> str:
    return f"fasttak-backup-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{fasttak_version()}.age"


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise BackupAlreadyRunning("a backup is already in progress") from exc
            raise
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def run(*, actor: str, client_ip: str | None) -> BackupResult:
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    lock_path = d / ".backup.lock"
    run_id = uuid.uuid4().hex[:12]
    started_at = _now()
    filename = _filename(started_at)
    final_path = d / filename
    partial_path = d / f"{filename}.partial"
    staging = Path(tempfile.mkdtemp(prefix=f"fasttak-backup-{run_id}-"))

    with _exclusive_lock(lock_path):
        state.write_last_run(
            {
                "started_at": started_at.isoformat(),
                "finished_at": None,
                "status": "running",
                "filename": filename,
                "error": None,
            }
        )
        record_event(
            source="audit",
            actor=actor,
            action="backup.started",
            target_type="backup",
            target_id=run_id,
            detail={"filename": filename, "fasttak_version": fasttak_version()},
            ip=client_ip,
        )
        try:
            _, recipient = load_or_create()
            postgres_versions = _collect_postgres_versions(databases=_database_dsns())
            manifest = build_manifest(
                created_at=started_at,
                hostname=socket.gethostname(),
                postgres_versions=postgres_versions,
            )
            _populate_staging(staging, manifest)
            _stream_encrypted_archive(staging, partial_path, recipient)
            os.replace(partial_path, final_path)
            _write_sha256_sidecar(final_path)
            size_bytes = final_path.stat().st_size
            finished_at = _now()
            state.write_last_run(
                {
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "status": "success",
                    "filename": filename,
                    "error": None,
                }
            )
            record_event(
                source="audit",
                actor=actor,
                action="backup.completed",
                target_type="backup",
                target_id=filename,
                detail={
                    "size_bytes": size_bytes,
                    "components": manifest["components"],
                },
                ip=client_ip,
            )
            for pruned_name in prune(keep=retention_keep()):
                record_event(
                    source="audit",
                    actor=actor,
                    action="backup.pruned",
                    target_type="backup",
                    target_id=pruned_name,
                    detail={"reason": "retention"},
                    ip=client_ip,
                )
            return BackupResult(filename=filename, size_bytes=size_bytes)
        except Exception as exc:
            _cleanup_partial(partial_path)
            finished_at = _now()
            # Log full exception (incl. message) to server logs; persist only
            # the exception type in state.json. pg_dump / psycopg / tar
            # stderr can include DSN fragments and filesystem paths, which
            # would otherwise leak into the dashboard's error banner (and
            # into anything that downloads state.json).
            log.exception("backup.run failed (filename=%s)", filename)
            state.write_last_run(
                {
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "status": "failed",
                    "filename": filename,
                    "error": type(exc).__name__,
                }
            )
            record_event(
                source="audit",
                actor=actor,
                action="backup.failed",
                target_type="backup",
                target_id=run_id,
                detail={"error": f"{type(exc).__name__}: {exc}"},
                ip=client_ip,
            )
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _cleanup_partial(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def _write_sha256_sidecar(archive: Path) -> None:
    """Write `<archive>.sha256` in `sha256sum -c` format alongside the archive.

    Lets operators verify integrity of an off-host copy without decrypting:
    `sha256sum -c fasttak-backup-...age.sha256`.
    """
    digest = hashlib.sha256()
    with archive.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    sidecar.write_text(f"{digest.hexdigest()}  {archive.name}\n")


def log_postgres_version_skew() -> None:
    """Best-effort warning if our bundled pg_dump major < either server major.

    Called at startup from main.py lifespan. All exceptions are swallowed —
    the DB may not be up yet, and the runner does the same check on every
    actual backup.
    """
    try:
        client_proc = subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True, check=True
        )
        client_major = _parse_pg_major(client_proc.stdout)
    except Exception:
        return
    for db in _database_dsns():
        try:
            with psycopg.connect(**db, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute("SHOW server_version")
                    row = cur.fetchone()
            server_major = _parse_pg_major(row[0] if row else "")
            if client_major and server_major and client_major < server_major:
                log.warning(
                    "pg_dump (major %s) is older than %s server (major %s); restores may fail",
                    client_major,
                    db["dbname"],
                    server_major,
                )
        except Exception:
            continue


def _parse_pg_major(s: str) -> int | None:
    """Extract the major version from strings like 'pg_dump (PostgreSQL) 17.0' or '15.5'."""
    match = re.search(r"(\d+)\.\d+", s)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\b", s)
    return int(match.group(1)) if match else None


# ── Workers (replaced by stubs in unit tests) ──────────────────────────


def _database_dsns() -> list[dict]:
    """DSN dicts in the order they appear in the manifest."""
    return [
        {
            "dbname": "cot",
            "host": "tak-database",
            "user": "martiuser",
            "password": settings.tak_db_password,
            "port": 5432,
        },
        {
            "dbname": "lldap",
            "host": "app-db",
            "user": "fastak",
            "password": settings.fastak_db_password,
            "port": 5432,
        },
        {
            "dbname": "nodered",
            "host": "app-db",
            "user": "fastak",
            "password": settings.fastak_db_password,
            "port": 5432,
        },
        {
            "dbname": "fastak",
            "host": "app-db",
            "user": "fastak",
            "password": settings.fastak_db_password,
            "port": 5432,
        },
    ]


def _populate_staging(staging: Path, manifest: dict) -> None:
    (staging / "postgres").mkdir(parents=True, exist_ok=True)
    (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    for db in _database_dsns():
        _dump_postgres(db, staging / "postgres" / f"{db['dbname']}.sql")
    _tar_certs(staging / "tak-certs.tar")
    _tar_tak_config(staging / "tak-config.tar")
    _tar_nodered_data(staging / "nodered-data.tar")
    _copy_env(staging / "env")


def _dump_postgres(dsn: dict, out: Path) -> None:
    env = {**os.environ, "PGPASSWORD": dsn["password"]}
    with open(out, "wb") as f:
        proc = subprocess.run(
            [
                "pg_dump",
                "-h",
                dsn["host"],
                "-p",
                str(dsn["port"]),
                "-U",
                dsn["user"],
                "-d",
                dsn["dbname"],
                "--format=plain",
                "--no-owner",
                "--no-privileges",
            ],
            env=env,
            stdout=f,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        raise RuntimeError(f"pg_dump {dsn['dbname']} failed: {stderr}")


def _tar_certs(out: Path) -> None:
    with open(out, "wb") as f:
        proc = subprocess.run(
            ["tar", "-c", "-C", "/tak-certs", "."],
            stdout=f,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"tar tak-certs failed: {proc.stderr.decode(errors='replace')}")


# Files inside tak/ that operator customization (or TAK Server's first-boot
# init) writes, that the release zip does not provide, and whose absence on
# a restored stack would silently revert configuration to defaults. Every
# file listed here MUST exist at backup time — if not, the running stack
# isn't in a state worth backing up (TAK Server has not finished its first
# boot) and a "successful" backup would produce an unrestorable archive.
_TAK_CONFIG_FILES = ("CoreConfig.xml", "TAKIgniteConfig.xml")
_TAK_SRC = "/tak-src"


def _tar_tak_config(out: Path, source_dir: str = _TAK_SRC) -> None:
    src = Path(source_dir)
    missing = [name for name in _TAK_CONFIG_FILES if not (src / name).exists()]
    if missing:
        raise RuntimeError(
            f"tak-config: required file(s) missing from {source_dir}: "
            + ", ".join(missing)
            + " — TAK Server has not finished first-boot init. Wait for the stack "
            "to become healthy before taking a backup."
        )
    with open(out, "wb") as f:
        proc = subprocess.run(
            ["tar", "-c", "-C", source_dir, *_TAK_CONFIG_FILES],
            stdout=f,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"tar tak-config failed: {proc.stderr.decode(errors='replace')}")


def _tar_nodered_data(out: Path) -> None:
    with open(out, "wb") as f:
        proc = subprocess.run(
            ["tar", "-c", "-C", "/nodered-data", "."],
            stdout=f,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"tar nodered-data failed: {proc.stderr.decode(errors='replace')}")


def _copy_env(out: Path) -> None:
    shutil.copyfile("/host/.env", out)


def _stream_encrypted_archive(staging: Path, out: Path, recipient: str) -> None:
    """Pipe `tar -c -C staging . | gzip | age -r <recipient> > out`."""
    tar_proc = subprocess.Popen(
        ["tar", "-c", "-C", str(staging), "."],
        stdout=subprocess.PIPE,
    )
    assert tar_proc.stdout is not None
    gzip_proc = subprocess.Popen(["gzip", "-c"], stdin=tar_proc.stdout, stdout=subprocess.PIPE)
    # Closing in the parent is required so tar's EOF propagates to gzip.
    tar_proc.stdout.close()
    try:
        with open(out, "wb") as f:
            age_proc = subprocess.run(
                ["age", "-r", recipient],
                stdin=gzip_proc.stdout,
                stdout=f,
                stderr=subprocess.PIPE,
            )
    finally:
        # Close the parent's copy of gzip's read pipe so gzip's writes
        # propagate cleanly via SIGPIPE if age exited early.
        if gzip_proc.stdout is not None:
            gzip_proc.stdout.close()

    if age_proc.returncode != 0:
        # age failed mid-stream. The upstream procs may still be blocked
        # writing into a pipe whose reader is gone; terminate them rather
        # than rely on SIGPIPE propagation through gzip's write buffer.
        for p in (gzip_proc, tar_proc):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        raise RuntimeError(f"age failed: {age_proc.stderr.decode(errors='replace')}")

    gzip_proc.wait()
    tar_proc.wait()
    if tar_proc.returncode != 0:
        raise RuntimeError("tar (staging) failed")
    if gzip_proc.returncode != 0:
        raise RuntimeError("gzip failed")
