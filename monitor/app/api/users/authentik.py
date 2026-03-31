"""Authentik REST API client for user and group management."""

import logging
import secrets
import time

import httpx

log = logging.getLogger(__name__)

_RETRIES = 3
_RETRY_DELAY = 2
_TIMEOUT = 30


class AuthentikClient:
    """Thin wrapper around Authentik's REST API."""

    def __init__(self, base_url: str, token: str, hidden_prefixes: list[str]):
        self.api_base = f"{base_url.rstrip('/')}/api/v3"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.hidden_prefixes = [p.lower() for p in hidden_prefixes]
        self._client = httpx.Client(timeout=_TIMEOUT)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with retry logic.

        4xx errors are not retried — they indicate a client mistake that won't
        resolve on its own. Only connection-level errors and 5xx are retried.
        """
        url = f"{self.api_base}/{path}"
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", _TIMEOUT)
        for attempt in range(1, _RETRIES + 1):
            try:
                r = self._client.request(method, url, **kwargs)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise  # 4xx: don't retry, propagate immediately
                log.warning("%s %s attempt %d/%d: %s", method, path, attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)
            except httpx.HTTPError as exc:
                log.warning("%s %s attempt %d/%d: %s", method, path, attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params).json()

    def _post(self, path: str, data: dict) -> dict:
        r = self._request("POST", path, json=data)
        return r.json() if r.text.strip() else {}

    def _patch(self, path: str, data: dict) -> dict:
        r = self._request("PATCH", path, json=data)
        return r.json() if r.text.strip() else {}

    def _delete(self, path: str) -> None:
        self._request("DELETE", path)

    def is_hidden(self, username: str) -> bool:
        lower = username.lower()
        return any(lower.startswith(p) for p in self.hidden_prefixes)

    def _format_user(self, u: dict) -> dict:
        """Convert Authentik user to API response shape."""
        attrs = u.get("attributes", {})
        groups_obj = u.get("groups_obj", [])
        tak_groups = [g["name"][4:] for g in groups_obj if g.get("name", "").startswith("tak_")]
        result = {
            "id": u["pk"],
            "username": u["username"],
            "name": u.get("name", ""),
            "is_active": u.get("is_active", True),
            "groups": tak_groups,
        }
        if "fastak_expires" in attrs:
            result["fastak_expires"] = attrs["fastak_expires"]
        if "fastak_certs_revoked" in attrs:
            result["fastak_certs_revoked"] = attrs["fastak_certs_revoked"]
        return result

    # ── Users ───────────────────────────────────────────────────────

    def list_users(self, search: str | None = None) -> list[dict]:
        """Fetch all users, filtering out hidden prefixes."""
        all_users = []
        page = 1
        while True:
            params = {"page": page, "page_size": 100}
            if search:
                params["search"] = search
            data = self._get("core/users/", params=params)
            for u in data.get("results", []):
                if not self.is_hidden(u["username"]):
                    all_users.append(self._format_user(u))
            if not data.get("pagination", {}).get("next"):
                break
            page += 1
        return all_users

    def get_user(self, user_id: int) -> dict | None:
        """Get a single user by pk. Returns None for hidden or missing users."""
        try:
            u = self._get(f"core/users/{user_id}/")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if self.is_hidden(u["username"]):
            return None
        return self._format_user(u)

    def create_user(
        self,
        username: str,
        name: str,
        ttl_hours: int | None = None,
        groups: list[str] | None = None,
        user_type: str | None = None,
    ) -> dict:
        """Create a passwordless user."""
        payload = {
            "username": username,
            "name": name,
            "is_active": True,
            "path": "users",
        }
        if user_type is not None:
            payload["type"] = user_type
        user = self._post("core/users/", payload)
        pk = user["pk"]

        attrs = {"fastak_certs_revoked": False}
        if ttl_hours is not None:
            attrs["fastak_expires"] = int(time.time() + ttl_hours * 3600)
        self._patch(f"core/users/{pk}/", {"attributes": attrs})

        if groups:
            for group_name in groups:
                tak_name = f"tak_{group_name}" if not group_name.startswith("tak_") else group_name
                self._add_user_to_group(pk, tak_name)

        return self._format_user({**user, "attributes": attrs, "groups_obj": []})

    def update_user(self, user_id: int, **kwargs) -> dict:
        """Update user fields. Handles reactivation, TTL changes."""
        patch_data = {}
        attrs_update = {}

        if "is_active" in kwargs:
            patch_data["is_active"] = kwargs["is_active"]
            if kwargs["is_active"]:
                attrs_update["fastak_certs_revoked"] = False

        if "ttl_hours" in kwargs:
            if kwargs["ttl_hours"] is None:
                # Clear both TTL attrs. Setting to None triggers pop() in the
                # merge loop below, which removes the key from Authentik attrs.
                attrs_update["fastak_expires"] = None
                attrs_update["fastak_certs_revoked"] = None
            else:
                attrs_update["fastak_expires"] = int(time.time() + kwargs["ttl_hours"] * 3600)
                if "fastak_certs_revoked" not in attrs_update:
                    attrs_update["fastak_certs_revoked"] = False

        if "name" in kwargs:
            patch_data["name"] = kwargs["name"]

        if attrs_update:
            user = self._get(f"core/users/{user_id}/")
            existing_attrs = user.get("attributes", {})
            for k, v in attrs_update.items():
                if v is None:
                    existing_attrs.pop(k, None)
                else:
                    existing_attrs[k] = v
            patch_data["attributes"] = existing_attrs

        if patch_data:
            self._patch(f"core/users/{user_id}/", patch_data)

        return self.get_user(user_id)

    def deactivate_user(self, user_id: int) -> None:
        """Deactivate user. Does NOT set fastak_certs_revoked — caller
        must do that after confirming cert revocation succeeded."""
        self._patch(f"core/users/{user_id}/", {"is_active": False})

    def set_password(self, user_id: int, password: str) -> None:
        self._post(f"core/users/{user_id}/set_password/", {"password": password})

    def mark_certs_revoked(self, user_id: int) -> None:
        """Set fastak_certs_revoked: true after cert cleanup confirmed."""
        user = self._get(f"core/users/{user_id}/")
        attrs = user.get("attributes", {})
        attrs["fastak_certs_revoked"] = True
        self._patch(f"core/users/{user_id}/", {"attributes": attrs})

    # ── Groups ──────────────────────────────────────────────────────

    def list_groups(self) -> list[dict]:
        groups = []
        page = 1
        while True:
            data = self._get(
                "core/groups/",
                params={"page": page, "page_size": 100, "search": "tak_"},
            )
            for g in data.get("results", []):
                name = g.get("name", "")
                if name.startswith("tak_") and name != "tak_ROLE_ADMIN":
                    groups.append({"id": g["pk"], "name": name[4:]})
            if not data.get("pagination", {}).get("next"):
                break
            page += 1
        return groups

    def get_group(self, group_id: str) -> dict | None:
        try:
            g = self._get(f"core/groups/{group_id}/")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        name = g.get("name", "")
        if not name.startswith("tak_"):
            return None
        members = [
            {"id": u["pk"], "username": u["username"]}
            for u in g.get("users_obj", [])
            if not self.is_hidden(u.get("username", ""))
        ]
        return {"id": g["pk"], "name": name[4:], "members": members}

    def create_group(self, name: str) -> dict:
        tak_name = f"tak_{name}" if not name.startswith("tak_") else name
        g = self._post("core/groups/", {"name": tak_name})
        return {"id": g["pk"], "name": tak_name[4:]}

    def delete_group(self, group_id: str) -> None:
        self._delete(f"core/groups/{group_id}/")

    def set_user_groups(self, user_id: int, group_names: list[str]) -> None:
        """Replace user's tak_-prefixed groups. Non-tak groups untouched."""
        user = self._get(f"core/users/{user_id}/")
        current_groups = user.get("groups_obj", [])
        non_tak_pks = [g["pk"] for g in current_groups if not g.get("name", "").startswith("tak_")]

        desired_tak_names = {f"tak_{n}" if not n.startswith("tak_") else n for n in group_names}

        # Paginate through all groups to find desired ones
        desired_pks = []
        page = 1
        while True:
            data = self._get("core/groups/", params={"page": page, "page_size": 100})
            for g in data.get("results", []):
                if g["name"] in desired_tak_names:
                    desired_pks.append(g["pk"])
            if not data.get("pagination", {}).get("next"):
                break
            page += 1

        self._patch(f"core/users/{user_id}/", {"groups": non_tak_pks + desired_pks})

    def _add_user_to_group(self, user_id: int, group_name: str) -> None:
        groups = self._get("core/groups/", params={"search": group_name})
        group = next((g for g in groups.get("results", []) if g["name"] == group_name), None)
        if group:
            self._post(f"core/groups/{group['pk']}/add_user/", {"pk": user_id})

    # ── Enrollment ──────────────────────────────────────────────────

    def delete_enrollment_tokens(self, user_id: int) -> int:
        """Delete all app_password tokens for a user. Returns count deleted."""
        user = self._get(f"core/users/{user_id}/")
        username = user["username"]

        tokens = self._get(
            "core/tokens/",
            params={"user__username": username, "intent": "app_password"},
        )
        count = 0
        for t in tokens.get("results", []):
            try:
                self._delete(f"core/tokens/{t['identifier']}/")
                count += 1
            except Exception:
                continue
        return count

    def get_or_create_enrollment_token(self, user_id: int, ttl_minutes: int) -> tuple[str, str]:
        """Get existing or create new app password. Returns (key, expiry_iso).

        The Authentik token API does not include the key in list or create
        responses. A separate GET to /core/tokens/{identifier}/view_key/ is
        required to retrieve the actual token value.
        """
        from datetime import UTC, datetime, timedelta

        # Get username for filtering (Authentik supports user__username, not user pk)
        user = self._get(f"core/users/{user_id}/")
        username = user["username"]

        tokens = self._get(
            "core/tokens/",
            params={
                "user__username": username,
                "intent": "app_password",
            },
        )
        now = time.time()
        for t in tokens.get("results", []):
            if t.get("expiring") and t.get("expires"):
                try:
                    exp = datetime.fromisoformat(t["expires"].replace("Z", "+00:00"))
                    if exp.timestamp() > now:
                        key_resp = self._get(f"core/tokens/{t['identifier']}/view_key/")
                        return key_resp["key"], t["expires"]
                except (ValueError, KeyError):
                    continue

        expires = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        identifier = f"enrollment-{user_id}-{secrets.token_hex(8)}"
        self._post(
            "core/tokens/",
            {
                "identifier": identifier,
                "intent": "app_password",
                "user": user_id,
                "expiring": True,
                "expires": expires.isoformat(),
            },
        )
        key_resp = self._get(f"core/tokens/{identifier}/view_key/")
        return key_resp["key"], expires.isoformat()

    # ── TTL queries ─────────────────────────────────────────────────

    def get_users_pending_expiry(self) -> list[dict]:
        """Get users with fastak_expires set and fastak_certs_revoked != true.
        Returns raw Authentik user dicts for the TTL task."""
        pending = []
        page = 1
        now = time.time()
        while True:
            data = self._get("core/users/", params={"page": page, "page_size": 100})
            for u in data.get("results", []):
                attrs = u.get("attributes", {})
                expires = attrs.get("fastak_expires")
                revoked = attrs.get("fastak_certs_revoked", False)
                if expires is not None and not revoked and expires <= now:
                    pending.append(u)
            if not data.get("pagination", {}).get("next"):
                break
            page += 1
        return pending
