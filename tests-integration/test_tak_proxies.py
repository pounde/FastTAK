"""Integration tests for /api/tak/* against a real Docker stack.

The test stack may or may not have connected clients / contacts; tests that
care about response structure assert shape but not content. Tests that care
about LKP enrichment short-circuit when the body is empty (no clients
connected during the test run).
"""

import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.integration


class TestTakProxies:
    def test_groups_returns_200(self, api):
        status, data = api("GET", "/api/tak/groups")
        assert status == 200
        assert isinstance(data, list)

    def test_clients_returns_200(self, api):
        status, data = api("GET", "/api/tak/clients")
        assert status == 200
        assert isinstance(data, list)

    def test_contacts_returns_200(self, api):
        status, data = api("GET", "/api/tak/contacts")
        assert status == 200
        assert isinstance(data, list)

    def test_missions_returns_200(self, api):
        status, data = api("GET", "/api/tak/missions")
        assert status == 200
        assert isinstance(data, list)

    def test_clients_with_lkp_returns_lkp_field(self, api):
        status, data = api("GET", "/api/tak/clients?include=lkp")
        assert status == 200
        assert isinstance(data, list)
        # Conditional check: only assert lkp field if any clients are connected.
        if data:
            assert "lkp" in data[0]

    def test_contacts_recent_returns_200(self, api):
        status, data = api("GET", "/api/tak/contacts/recent?max_age=86400")
        assert status == 200
        assert isinstance(data, list)
        if data:
            assert "lkp" in data[0]  # may be None


# ---------------------------------------------------------------------------
# Helpers for cot_router seeding
# ---------------------------------------------------------------------------
# TAK Server's PostgreSQL uses peer auth on the Unix socket, so we cannot
# connect as 'martiuser' directly from a subprocess.  Running psql under the
# 'postgres' OS user (which owns the socket) bypasses this.


def _psql(compose_cmd: list[str], sql: str) -> None:
    """Run a SQL statement inside tak-database as the postgres OS user.

    SQL must not contain double-quote characters; the statement is passed
    to ``sh -c 'psql -d cot -c "<sql>"'`` and double-quotes in the SQL
    will break shell parsing. Wrap PostgreSQL identifiers with backticks
    or use unquoted forms instead.
    """
    subprocess.run(
        [
            *compose_cmd,
            "exec",
            "-T",
            "tak-database",
            "su",
            "-s",
            "/bin/sh",
            "postgres",
            "-c",
            f'psql -d cot -c "{sql}"',
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )


def _seed_cot_router(compose_cmd: list[str], rows: list[tuple]) -> None:
    """Insert synthetic rows into cot_router via psql.

    Each row: (uid, cot_type, lat, lon, detail_xml).
    XML attribute values must use single quotes to avoid shell-escaping issues
    (the SQL literal is itself single-quoted; any single quotes in detail_xml
    are SQL-escaped as '').
    """
    sql_parts = []
    for uid, cot_type, lat, lon, detail in rows:
        # SQL string literal: escape embedded single quotes as ''
        detail_escaped = detail.replace("'", "''")
        sql_parts.append(
            f"INSERT INTO cot_router (uid, cot_type, servertime, event_pt, detail) "
            f"VALUES ('{uid}', '{cot_type}', NOW(), "
            f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326), '{detail_escaped}');"
        )
    _psql(compose_cmd, " ".join(sql_parts))


def _delete_cot_rows(compose_cmd: list[str], uids: list[str]) -> None:
    if not uids:
        return
    quoted = ",".join(f"'{u}'" for u in uids)
    try:
        _psql(compose_cmd, f"DELETE FROM cot_router WHERE uid IN ({quoted});")
    except subprocess.CalledProcessError as exc:
        # best-effort cleanup; surface so orphaned rows don't accrete silently
        stderr_text = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        print(f"_delete_cot_rows cleanup failed (ignored): {stderr_text}", file=sys.stderr)


class TestRecentContactsLkpPersistence:
    def test_cot_router_uid_appears_with_detail_callsign(self, api, compose_cmd):
        """Synthetic UID not in /contacts/all must still render with callsign from detail XML."""
        ground_uid = f"FASTTAK-TEST-GROUND-{uuid.uuid4().hex[:8]}"
        sensor_uid = f"FASTTAK-TEST-SENSOR-{uuid.uuid4().hex[:8]}"
        # Use single quotes for XML attribute values to avoid shell-escaping
        # inside the psql -c "..." string.
        ground_detail = (
            "<detail>"
            "<contact callsign='TestUnit'/>"
            "<__group name='Cyan' role='Team Member'/>"
            "</detail>"
        )
        try:
            _seed_cot_router(
                compose_cmd,
                [
                    (ground_uid, "a-f-G-U-C-I", 38.8, -77.0, ground_detail),
                    (sensor_uid, "b-m-p-s-p-i", 38.9, -77.1, "<detail/>"),
                ],
            )

            status, data = api("GET", "/api/tak/contacts/recent?max_age=86400")
            assert status == 200
            assert isinstance(data, list)
            by_uid = {c["uid"]: c for c in data}

            # Ground-unit synthetic UID renders with detail-XML enrichment
            assert ground_uid in by_uid, (
                f"Expected synthetic ground UID in response. UIDs returned: {list(by_uid)[:10]}"
            )
            entry = by_uid[ground_uid]
            assert entry["callsign"] == "TestUnit"
            assert entry["team"] == "Cyan"
            assert entry["role"] == "Team Member"
            assert entry["lkp"]["lat"] == pytest.approx(38.8)
            assert entry["lkp"]["lon"] == pytest.approx(-77.0)

            # Sensor marker (b-m-p-s-p-i) is excluded by allowlist
            assert sensor_uid not in by_uid

        finally:
            _delete_cot_rows(compose_cmd, [ground_uid, sensor_uid])
