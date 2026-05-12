"""`python -m app.backup` — CLI entrypoint.

Subcommands:
    run             — perform a backup. Exit non-zero on failure.
    list            — list backups currently on disk.
    prune --keep N  — manually run retention.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from app.backup import retention, runner
from app.backup.config import backup_dir, retention_keep
from app.backup.exceptions import BackupAlreadyRunning


def _cmd_run(args: argparse.Namespace) -> int:
    # Spec: CLI actor falls back to "system" if LOGNAME is unset. Inside
    # `docker compose exec` LOGNAME is "root", which is technically true
    # but useless for audit. Operators can override with --actor.
    actor = args.actor or os.environ.get("LOGNAME") or "system"
    try:
        result = runner.run(actor=actor, client_ip=None)
    except BackupAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {result.filename} ({result.size_bytes} bytes)")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    d = backup_dir()
    files = sorted(
        d.glob("fasttak-backup-*.age"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        print("(no backups)")
        return 0
    now = time.time()
    for p in files:
        st = p.stat()
        age_minutes = (now - st.st_mtime) / 60
        print(f"{p.name}\t{st.st_size}\t{age_minutes:.1f}m ago")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    keep = args.keep if args.keep is not None else retention_keep()
    deleted = retention.prune(keep=keep)
    for name in deleted:
        print(f"pruned: {name}")
    print(f"({len(deleted)} removed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.backup")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run", help="Perform a backup")
    run_parser.add_argument(
        "--actor",
        default=None,
        help="Override audit actor (default: $LOGNAME or 'system')",
    )
    run_parser.set_defaults(func=_cmd_run)
    sub.add_parser("list", help="List backups").set_defaults(func=_cmd_list)

    prune_parser = sub.add_parser("prune", help="Manually run retention")
    prune_parser.add_argument("--keep", type=int, default=None)
    prune_parser.set_defaults(func=_cmd_prune)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
