"""Reusable FastAPI auth dependencies.

This module is the home for cross-cutting authorization helpers. The first
consumer is the backup router; future admin-only endpoints reuse the same
factory rather than each one inventing its own group check.

The group name comes from an env var, so operators can rename groups
without code changes. The env var is read on every request so changes
take effect without restarting the monitor.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import HTTPException, Request


def require_group(env_var: str, *, default: str) -> Callable[[Request], None]:
    """Return a FastAPI dependency that 403s if the user is not in the group named by `env_var`.

    The dependency reads `request.state.groups`, which `AuthContextMiddleware`
    (app/audit.py) populates from the `Remote-Groups` header set by Caddy's
    `forward_auth` plus `copy_headers`. If the middleware did not run for a
    given request (e.g. unit tests without it), `groups` defaults to `[]`
    and the dependency rejects.
    """

    def _dep(request: Request) -> None:
        required = os.environ.get(env_var, default)
        groups = getattr(request.state, "groups", []) or []
        if required not in groups:
            raise HTTPException(
                status_code=403,
                detail=f"requires group membership: {required}",
            )

    return _dep
