"""HTTP API for backup management.

All routes are gated by `require_group("BACKUP_ADMIN_GROUP")` (default
group: `monitor_admin`). The handlers stream files using FileResponse and
delegate the heavy work to the runner module.

Audit: every read AND every state-changing route emits a row in
`fastak_events` via `app.audit.record_event`. The middleware records a
generic row too; ours adds the structured detail (filename, size).
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.auth_deps import require_group
from app.audit import record_event
from app.backup import keys, state
from app.backup.config import admin_group_default, backup_dir
from app.backup.exceptions import BackupAlreadyRunning
from app.backup.runner import run as run_backup

log = logging.getLogger(__name__)

# fasttak-backup-YYYYMMDDTHHMMSSZ-vX.Y.Z.age (version may include hyphens for prereleases).
# Strict shape so an attacker can't slip in odd filenames that pass _safe_filename.
# The version slot must start with an alphanumeric and contain no consecutive
# dots — the entire filename is a single path component so `..` can't traverse
# directories on its own, but rejecting it keeps the surface tight.
_FILENAME_RE = re.compile(
    r"^fasttak-backup-\d{8}T\d{6}Z-(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*\.age$"
)

require_admin = require_group("BACKUP_ADMIN_GROUP", default=admin_group_default())

router = APIRouter(prefix="/api/backup", tags=["backup"], dependencies=[Depends(require_admin)])


def _safe_filename(name: str) -> str:
    if not _FILENAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid backup filename")
    return name


@router.get("/")
def list_backups():
    d = backup_dir()
    files = sorted(d.glob("fasttak-backup-*.age"), key=lambda p: p.stat().st_mtime, reverse=True)
    now = time.time()
    return {
        "state": state.read(),
        "backups": [
            {
                "filename": p.name,
                "size_bytes": p.stat().st_size,
                "age_seconds": int(now - p.stat().st_mtime),
            }
            for p in files
        ],
    }


@router.get("/status")
def status():
    return state.read()


@router.post("/run")
def trigger_run(request: Request, background_tasks: BackgroundTasks):
    actor = getattr(request.state, "username", "unknown")
    client_ip = getattr(request.state, "client_ip", None)
    job_id = uuid.uuid4().hex[:12]

    def _run_in_thread():
        try:
            run_backup(actor=actor, client_ip=client_ip)
        except BackupAlreadyRunning:
            # Preflight returned 202 but another run grabbed the lock before
            # we got to it. Persist a failed state AND emit matching audit
            # events so the trail in fastak_events is symmetric with the
            # success and runner-internal-failure paths (both of which emit
            # backup.started + backup.completed/backup.failed).
            log.warning("backup.run dispatched while another run held the lock (job=%s)", job_id)
            from datetime import UTC, datetime

            from app.backup import state as _state

            now = datetime.now(UTC).isoformat()
            _state.write_last_run(
                {
                    "started_at": now,
                    "finished_at": now,
                    "status": "failed",
                    "filename": "",
                    "error": "BackupAlreadyRunning",
                }
            )
            record_event(
                source="audit",
                actor=actor,
                action="backup.started",
                target_type="backup",
                target_id=job_id,
                detail={},
                ip=client_ip,
            )
            record_event(
                source="audit",
                actor=actor,
                action="backup.failed",
                target_type="backup",
                target_id=job_id,
                detail={
                    "error": "BackupAlreadyRunning",
                    "reason": "lock_held_at_dispatch",
                },
                ip=client_ip,
            )
        except Exception:
            log.exception("backup.run failed (job=%s)", job_id)

    # Pre-flight: try to take and immediately release the lock so we can
    # return 409 synchronously when another run is in progress.
    try:
        run_backup_preflight()
    except BackupAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # FastAPI runs sync background-task functions in the threadpool. The
    # runner is fully blocking; no asyncio wrapping required.
    background_tasks.add_task(_run_in_thread)
    return JSONResponse(status_code=202, content={"job_id": job_id})


def run_backup_preflight() -> None:
    """Try to take and immediately release the backup lock.

    Used by the API to surface 409 synchronously without waiting for the
    background task to start. Raises `BackupAlreadyRunning` if held.
    """
    import errno
    import fcntl
    import os

    lock_path = backup_dir() / ".backup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise BackupAlreadyRunning("a backup is already in progress") from exc
        raise
    finally:
        os.close(fd)


@router.get("/files/{filename:path}")
def download(filename: str, request: Request):
    safe = _safe_filename(filename)
    path = backup_dir() / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    record_event(
        source="audit",
        actor=getattr(request.state, "username", "unknown"),
        action="backup.downloaded",
        target_type="backup",
        target_id=safe,
        detail={"size_bytes": path.stat().st_size},
        ip=getattr(request.state, "client_ip", None),
    )
    return FileResponse(path, media_type="application/octet-stream", filename=safe)


@router.delete("/files/{filename:path}", status_code=204)
def delete(filename: str, request: Request):
    safe = _safe_filename(filename)
    path = backup_dir() / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    path.unlink()
    record_event(
        source="audit",
        actor=getattr(request.state, "username", "unknown"),
        action="backup.deleted",
        target_type="backup",
        target_id=safe,
        detail={},
        ip=getattr(request.state, "client_ip", None),
    )


@router.get("/key")
def download_key(request: Request):
    raw = keys.read_identity()
    if raw is None:
        raise HTTPException(status_code=404, detail="no key yet — take a backup first")
    state.mark_key_downloaded()
    record_event(
        source="audit",
        actor=getattr(request.state, "username", "unknown"),
        action="backup.key_downloaded",
        target_type="backup_key",
        target_id="",
        detail={},
        ip=getattr(request.state, "client_ip", None),
    )
    headers = {"Content-Disposition": 'attachment; filename="fasttak-backup-key.txt"'}
    return Response(content=raw, media_type="text/plain", headers=headers)
