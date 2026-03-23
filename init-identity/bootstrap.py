#!/usr/bin/env python3
"""Authentik LDAP bootstrap for FastTAK.

Runs as an init container after Authentik is healthy. Creates the LDAP
service account, configures the LDAP authentication flow with 3 stages,
creates the LDAP provider/application/outpost, optionally creates a
webadmin user, and generates TAK Portal settings.

Idempotent — safe to run multiple times.

Environment variables:
    AUTHENTIK_URL          — Authentik internal URL (default: http://authentik-server:9000)
    AUTHENTIK_API_TOKEN    — bootstrap API token (required)
    LDAP_BIND_PASSWORD     — password for adm_ldapservice account (required)
    TAK_WEBADMIN_PASSWORD  — password for webadmin user (optional)
    LDAP_HOST              — LDAP outpost hostname (default: authentik-ldap)
    LDAP_BASE_DN           — LDAP base DN (default: DC=takldap)
"""

import os
import re
import sys
import time
import logging

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "http://authentik-server:9000").rstrip("/")
API_BASE = f"{AUTHENTIK_URL}/api/v3"

LDAP_SERVICE_USER = "adm_ldapservice"
LDAP_HOST = os.environ.get("LDAP_HOST", "authentik-ldap")
LDAP_APP_SLUG = "ldap"
LDAP_APP_NAME = "LDAP"
LDAP_PROVIDER_NAME = "LDAP"
LDAP_OUTPOST_NAME = "LDAP Outpost"

TAK_DIR = "/opt/tak"
CONFIG = f"{TAK_DIR}/CoreConfig.xml"

READY_TIMEOUT = 300
READY_INTERVAL = 5
API_RETRIES = 3
API_RETRY_DELAY = 5

log = logging.getLogger("bootstrap")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def env_required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        log.error("Required env var %s is not set", name)
        sys.exit(1)
    return val


HEADERS: dict = {}
TIMEOUT = 60


def api_get(path: str, params: dict | None = None):
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.get(f"{API_BASE}/{path}", headers=HEADERS, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("GET %s attempt %d/%d failed: %s", path, attempt, API_RETRIES, exc)
            if attempt == API_RETRIES:
                raise
            time.sleep(API_RETRY_DELAY)


def api_post(path: str, data: dict):
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.post(f"{API_BASE}/{path}", json=data, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json() if r.text.strip() else {}
        except requests.RequestException as exc:
            log.warning("POST %s attempt %d/%d failed: %s", path, attempt, API_RETRIES, exc)
            if attempt == API_RETRIES:
                raise
            time.sleep(API_RETRY_DELAY)


def api_patch(path: str, data: dict):
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.patch(f"{API_BASE}/{path}", json=data, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return
        except requests.RequestException as exc:
            log.warning("PATCH %s attempt %d/%d failed: %s", path, attempt, API_RETRIES, exc)
            if attempt == API_RETRIES:
                raise
            time.sleep(API_RETRY_DELAY)


def api_delete(path: str):
    try:
        requests.delete(f"{API_BASE}/{path}", headers=HEADERS, timeout=TIMEOUT)
    except Exception:
        pass


def find_stage(api_path: str, name: str):
    """Find a stage by name, paginating through results."""
    for page in range(1, 4):
        data = api_get(f"{api_path}?page={page}&page_size=100")
        for s in data.get("results", []):
            if s.get("name") == name:
                return s.get("pk")
        if not data.get("pagination", {}).get("next"):
            break
    return None


def find_or_create_stage(api_path: str, name: str, attrs: dict):
    """Find existing stage or create it."""
    pk = find_stage(api_path, name)
    if pk:
        return pk
    try:
        result = api_post(api_path, {"name": name, **attrs})
        return result.get("pk")
    except requests.HTTPError:
        return find_stage(api_path, name)


# ---------------------------------------------------------------------------
# Step 1: Wait for Authentik
# ---------------------------------------------------------------------------


def wait_for_authentik() -> None:
    log.info("Waiting for Authentik API at %s ...", AUTHENTIK_URL)
    for _ in range(60):
        try:
            api_get("core/users/?page_size=1")
            log.info("Authentik API is ready.")
            return
        except Exception:
            time.sleep(READY_INTERVAL)
    log.error("Authentik API not reachable after %ds", READY_TIMEOUT)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 2: Create/ensure LDAP service account
# ---------------------------------------------------------------------------


def ensure_service_account(ldap_password: str) -> int:
    """Create the LDAP service account if it doesn't exist. Return user pk."""
    log.info("Ensuring LDAP service account...")
    users = api_get("core/users/?search=adm_ldapservice").get("results", [])
    user = next((u for u in users if u.get("username") == LDAP_SERVICE_USER), None)

    if not user:
        user = api_post("core/users/", {
            "username": LDAP_SERVICE_USER,
            "name": "LDAP Service Account",
            "is_active": True,
            "type": "service_account",
            "path": "users",
        })
        log.info("Created %s (pk=%s)", LDAP_SERVICE_USER, user["pk"])
    else:
        # Ensure path is 'users' (not 'service-accounts') for correct LDAP DN
        patches = {}
        if not user.get("is_active", True):
            patches["is_active"] = True
        if user.get("path", "") != "users":
            patches["path"] = "users"
        if patches:
            api_patch(f"core/users/{user['pk']}/", patches)
        log.info("%s exists (pk=%s)", LDAP_SERVICE_USER, user["pk"])

    uid = user["pk"]

    # Set password
    api_post(f"core/users/{uid}/set_password/", {"password": ldap_password})

    # Add to authentik Admins group
    groups = api_get("core/groups/?search=authentik+Admins").get("results", [])
    admins = next((g for g in groups if "admins" in g.get("name", "").lower()), None)
    if admins:
        member_pks = [
            u.get("pk") if isinstance(u, dict) else u
            for u in (admins.get("users") or [])
        ]
        if uid not in member_pks:
            api_post(f"core/groups/{admins['pk']}/add_user/", {"pk": uid})
            log.info("Added %s to authentik Admins", LDAP_SERVICE_USER)
    else:
        log.warning("authentik Admins group not found")

    return uid


# ---------------------------------------------------------------------------
# Step 3: Create/ensure webadmin user
# ---------------------------------------------------------------------------


def ensure_webadmin_user() -> None:
    """Create webadmin user in Authentik with tak_ROLE_ADMIN group."""
    webadmin_pass = os.environ.get("TAK_WEBADMIN_PASSWORD", "").strip()
    if not webadmin_pass:
        log.info("No TAK_WEBADMIN_PASSWORD set, skipping webadmin user")
        return

    log.info("Ensuring webadmin user...")
    users = api_get("core/users/?search=webadmin").get("results", [])
    user = next((u for u in users if u.get("username") == "webadmin"), None)

    if not user:
        user = api_post("core/users/", {
            "username": "webadmin",
            "name": "TAK Web Admin",
            "is_active": True,
            "path": "users",
        })
        log.info("Created webadmin (pk=%s)", user["pk"])

    # Set password
    api_post(f"core/users/{user['pk']}/set_password/", {"password": webadmin_pass})

    # Add to tak_ROLE_ADMIN group (create if needed)
    groups = api_get("core/groups/?search=tak_ROLE_ADMIN").get("results", [])
    admin_group = next((g for g in groups if g.get("name") == "tak_ROLE_ADMIN"), None)
    if not admin_group:
        admin_group = api_post("core/groups/", {"name": "tak_ROLE_ADMIN"})
        log.info("Created tak_ROLE_ADMIN group")

    member_pks = [
        u.get("pk") if isinstance(u, dict) else u
        for u in (admin_group.get("users") or [])
    ]
    if user["pk"] not in member_pks:
        api_post(f"core/groups/{admin_group['pk']}/add_user/", {"pk": user["pk"]})


# ---------------------------------------------------------------------------
# Step 3b: Create/ensure nodered service user
# ---------------------------------------------------------------------------


def ensure_nodered_user() -> None:
    """Create nodered user in Authentik with tak_ROLE_ADMIN group.

    Node-RED connects to TAK Server via client cert (CN=nodered). TAK Server
    looks up the CN in LDAP to determine group membership. Without a matching
    LDAP user, CoT messages from Node-RED flows are silently dropped because
    TAK Server can't route them to any group.
    """
    log.info("Ensuring nodered user...")
    users = api_get("core/users/?search=nodered").get("results", [])
    user = next((u for u in users if u.get("username") == "nodered"), None)

    if not user:
        user = api_post("core/users/", {
            "username": "nodered",
            "name": "Node-RED Service Account",
            "is_active": True,
            "type": "service_account",
            "path": "users",
        })
        log.info("Created nodered user (pk=%s)", user["pk"])
    else:
        log.info("nodered user exists (pk=%s)", user["pk"])

    # Add to tak_ROLE_ADMIN group (create if needed)
    groups = api_get("core/groups/?search=tak_ROLE_ADMIN").get("results", [])
    admin_group = next((g for g in groups if g.get("name") == "tak_ROLE_ADMIN"), None)
    if not admin_group:
        admin_group = api_post("core/groups/", {"name": "tak_ROLE_ADMIN"})
        log.info("Created tak_ROLE_ADMIN group")

    member_pks = [
        u.get("pk") if isinstance(u, dict) else u
        for u in (admin_group.get("users") or [])
    ]
    if user["pk"] not in member_pks:
        api_post(f"core/groups/{admin_group['pk']}/add_user/", {"pk": user["pk"]})
        log.info("Added nodered to tak_ROLE_ADMIN")


# ---------------------------------------------------------------------------
# Step 4: Configure LDAP authentication flow with 3 stages
# ---------------------------------------------------------------------------


def ensure_ldap_flow() -> str:
    """Create or update the LDAP authentication flow with 3 stages. Return flow pk."""
    log.info("Configuring LDAP authentication flow...")

    flows = api_get("flows/instances/?slug=ldap-authentication-flow").get("results", [])

    if flows:
        flow = flows[0]
        api_patch("flows/instances/ldap-authentication-flow/", {"authentication": "none"})
    else:
        flow = api_post("flows/instances/", {
            "name": "ldap-authentication-flow",
            "slug": "ldap-authentication-flow",
            "title": "ldap-authentication-flow",
            "designation": "authentication",
            "authentication": "none",
            "layout": "stacked",
            "denied_action": "message_continue",
            "policy_engine_mode": "any",
        })
        log.info("Created ldap-authentication-flow")

    flow_pk = flow["pk"]

    # Check existing bindings
    all_bindings = []
    page = 1
    while True:
        data = api_get(f"flows/bindings/?ordering=order&page_size=500&page={page}")
        all_bindings.extend(data.get("results", []))
        if not data.get("pagination", {}).get("next"):
            break
        page += 1

    flow_bindings = [b for b in all_bindings if str(b.get("target")) == str(flow_pk)]
    stage_names = {(b.get("stage_obj") or {}).get("name", "") for b in flow_bindings}
    need_names = {"ldap-identification-stage", "ldap-authentication-password", "ldap-authentication-login"}

    if len(flow_bindings) < 3 or need_names != stage_names:
        # Delete wrong bindings
        for b in flow_bindings:
            api_delete(f"flows/bindings/{b['pk']}/")

        # Find or create the 3 LDAP stages
        id_stage = find_or_create_stage("stages/identification/", "ldap-identification-stage", {
            "case_insensitive_matching": True,
            "pretend_user_exists": True,
            "show_matched_user": True,
            "user_fields": ["username"],
        })
        pw_stage = find_or_create_stage("stages/password/", "ldap-authentication-password", {
            "backends": ["authentik.core.auth.InbuiltBackend", "authentik.core.auth.TokenBackend"],
            "failed_attempts_before_cancel": 5,
        })
        login_stage = find_or_create_stage("stages/user_login/", "ldap-authentication-login", {
            "session_duration": "seconds=0",
            "remember_me_offset": "seconds=0",
        })

        if not all([id_stage, pw_stage, login_stage]):
            log.error("Could not create stages: id=%s pw=%s login=%s", id_stage, pw_stage, login_stage)
            sys.exit(1)

        # Bind stages to flow
        for order, stage_pk in [(10, id_stage), (15, pw_stage), (20, login_stage)]:
            try:
                api_post("flows/bindings/", {
                    "target": flow_pk,
                    "stage": stage_pk,
                    "order": order,
                    "evaluate_on_plan": True,
                    "re_evaluate_policies": True,
                    "policy_engine_mode": "any",
                    "invalid_response_action": "retry",
                })
            except requests.HTTPError:
                pass  # binding may already exist

        log.info("LDAP flow stages bound")

    # Clear password_stage on identification stage (prevents recursion depth error)
    id_stage_pk = find_stage("stages/identification/", "ldap-identification-stage")
    if id_stage_pk:
        try:
            api_patch(f"stages/identification/{id_stage_pk}/", {
                "password_stage": None,
                "user_fields": ["username"],
            })
        except Exception:
            pass

    return flow_pk


# ---------------------------------------------------------------------------
# Step 5: Create LDAP provider, application, outpost
# ---------------------------------------------------------------------------


def get_invalidation_flow() -> str:
    """Return the pk of the default invalidation flow."""
    flows = api_get("flows/instances/?designation=invalidation").get("results", [])
    inv = next((f for f in flows if f.get("slug") == "default-provider-invalidation-flow"), None)
    if not inv and flows:
        inv = flows[0]
    if not inv:
        log.error("No invalidation flow found in Authentik.")
        sys.exit(1)
    return inv["pk"]


def ensure_ldap_provider(base_dn: str, flow_pk: str) -> int:
    """Create LDAP provider if it doesn't exist. Return provider pk."""
    providers = api_get("providers/ldap/?search=LDAP").get("results", [])
    ldap_prov = next((p for p in providers if p.get("name") == LDAP_PROVIDER_NAME), None)

    if ldap_prov:
        pk = ldap_prov["pk"]
        api_patch(f"providers/ldap/{pk}/", {
            "authentication_flow": flow_pk,
            "authorization_flow": flow_pk,
        })
        log.info("LDAP provider exists (pk=%s), updated flow", pk)
        return pk

    invalidation_flow = get_invalidation_flow()
    log.info("Creating LDAP provider...")
    r = api_post("providers/ldap/", {
        "name": LDAP_PROVIDER_NAME,
        "authorization_flow": flow_pk,
        "authentication_flow": flow_pk,
        "invalidation_flow": invalidation_flow,
        "base_dn": base_dn,
        "bind_mode": "cached",
        "search_mode": "cached",
    })
    pk = r["pk"]
    log.info("LDAP provider created (pk=%s)", pk)
    return pk


def ensure_ldap_application(provider_pk: int) -> str:
    """Create application linked to the LDAP provider. Return slug."""
    r = api_get("core/applications/", params={"slug": LDAP_APP_SLUG})
    results = r.get("results", [])
    if results:
        log.info("Application '%s' already exists.", LDAP_APP_SLUG)
        return results[0]["slug"]

    log.info("Creating application '%s' ...", LDAP_APP_SLUG)
    api_post("core/applications/", {
        "name": LDAP_APP_NAME,
        "slug": LDAP_APP_SLUG,
        "provider": provider_pk,
    })
    log.info("Application '%s' created.", LDAP_APP_SLUG)
    return LDAP_APP_SLUG


def ensure_ldap_outpost(provider_pk: int) -> None:
    """Create LDAP outpost linked to the provider. Idempotent."""
    r = api_get("outposts/instances/", params={"name": LDAP_OUTPOST_NAME})
    results = r.get("results", [])

    if results:
        outpost = results[0]
        outpost_pk = outpost["pk"]
        linked = outpost.get("providers", [])
        if provider_pk not in linked:
            log.info("Outpost exists but provider not linked — fixing.")
            api_patch(f"outposts/instances/{outpost_pk}/", {"providers": [provider_pk]})
        else:
            log.info("Outpost '%s' already exists and linked.", LDAP_OUTPOST_NAME)
        return

    log.info("Creating outpost '%s' ...", LDAP_OUTPOST_NAME)
    api_post("outposts/instances/", {
        "name": LDAP_OUTPOST_NAME,
        "type": "ldap",
        "providers": [provider_pk],
        "config": {
            "authentik_host": AUTHENTIK_URL,
            "log_level": "info",
        },
    })
    log.info("Outpost '%s' created and linked.", LDAP_OUTPOST_NAME)


# ---------------------------------------------------------------------------
# Step 6: Generate TAK Portal configuration
# ---------------------------------------------------------------------------


def configure_tak_portal(token: str) -> None:
    """Write settings.json and copy certs for TAK Portal."""
    import json
    import shutil

    portal_dir = f"{TAK_DIR}/portal"
    certs_dir = f"{portal_dir}/certs"
    settings_path = f"{portal_dir}/settings.json"

    os.makedirs(certs_dir, exist_ok=True)

    # Copy TAK CA cert
    ca_src = f"{TAK_DIR}/certs/files/ca.pem"
    ca_dst = f"{certs_dir}/tak-ca.pem"
    if os.path.isfile(ca_src):
        shutil.copy2(ca_src, ca_dst)
        log.info("Copied CA cert to %s", ca_dst)
    else:
        log.warning("CA cert not found at %s — portal cert features may not work", ca_src)

    # Copy admin.p12 for TAK API access
    p12_src = f"{TAK_DIR}/certs/files/admin.p12"
    p12_dst = f"{certs_dir}/webadmin.p12"
    if os.path.isfile(p12_src):
        shutil.copy2(p12_src, p12_dst)
        log.info("Copied admin.p12 to %s", p12_dst)
    else:
        log.warning("admin.p12 not found at %s — portal TAK API access may not work", p12_src)

    fqdn = os.environ.get("FQDN", "localhost")
    portal_subdomain = os.environ.get("TAKPORTAL_SUBDOMAIN", "portal")
    authentik_subdomain = os.environ.get("AUTHENTIK_SUBDOMAIN", "auth")

    settings = {
        "AUTHENTIK_URL": AUTHENTIK_URL,
        "AUTHENTIK_TOKEN": token,
        "AUTHENTIK_PUBLIC_URL": f"https://{authentik_subdomain}.{fqdn}",
        "TAK_PORTAL_PUBLIC_URL": f"https://{portal_subdomain}.{fqdn}",
        "USERS_HIDDEN_PREFIXES": "ak-,adm_,nodered-,ma-",
        "GROUPS_HIDDEN_PREFIXES": "authentik, MA -",
        "USERS_ACTIONS_HIDDEN_PREFIXES": "",
        "GROUPS_ACTIONS_HIDDEN_PREFIXES": "",
        "DASHBOARD_AUTHENTIK_STATS_REFRESH_SECONDS": "300",
        "PORTAL_AUTH_ENABLED": "false",
        "PORTAL_AUTH_REQUIRED_GROUP": "",
        "TAK_URL": "https://tak-server:8443/Marti",
        "TAK_API_P12_PATH": "./data/certs/webadmin.p12",
        "TAK_API_P12_PASSPHRASE": "atakatak",
        "TAK_CA_PATH": "./data/certs/tak-ca.pem",
        "TAK_REVOKE_ON_DISABLE": "true",
        "TAK_DEBUG": "false",
        "TAK_BYPASS_ENABLED": "false",
        "CLOUDTAK_URL": "",
        "EMAIL_ENABLED": "false",
    }

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    log.info("TAK Portal settings.json written to %s", settings_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    global HEADERS
    log.info("=== Identity bootstrap starting ===")

    token = env_required("AUTHENTIK_API_TOKEN")
    ldap_password = env_required("LDAP_BIND_PASSWORD")
    base_dn = os.environ.get("LDAP_BASE_DN", "DC=takldap").strip() or "DC=takldap"

    HEADERS = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # 1. Wait for Authentik
    wait_for_authentik()

    # 2. LDAP service account
    ensure_service_account(ldap_password)

    # 3. Webadmin user
    ensure_webadmin_user()

    # 3b. Node-RED service user (maps nodered cert CN to LDAP group)
    ensure_nodered_user()

    # 4. LDAP authentication flow with 3 stages
    flow_pk = ensure_ldap_flow()

    # 5. LDAP provider, application, outpost
    provider_pk = ensure_ldap_provider(base_dn, flow_pk)
    ensure_ldap_application(provider_pk)
    ensure_ldap_outpost(provider_pk)

    # 6. Generate TAK Portal config
    configure_tak_portal(token)

    log.info("=== Identity bootstrap complete ===")


if __name__ == "__main__":
    main()
