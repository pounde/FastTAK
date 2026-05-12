"""FastTAK backup module.

Public surface:
- `runner.run()` performs one backup end-to-end.
- `keys.load_or_create()` / `keys.read_identity()` handle the age identity.
- `state.read()` / `state.write_last_run()` track operational status.
- `retention.prune()` enforces keep-N.

CLI: `python -m app.backup`. HTTP: see `app/api/backup/router.py`.
"""
