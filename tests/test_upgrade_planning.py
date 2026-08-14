# tests/test_upgrade_planning.py
"""Planning helpers in scripts/upgrade.sh.

Everything here runs with FASTAK_UPGRADE_LIB_ONLY=1, which sources the script's
helper definitions and returns before it touches Docker. The migration itself
moves Docker volumes and is covered by tests-integration/test_upgrade_rehearsal.py.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UPGRADE = REPO / "scripts" / "upgrade.sh"

SOURCE = f'FASTAK_UPGRADE_LIB_ONLY=1 . "{UPGRADE}"'


def _bash(script: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Source upgrade.sh's helpers, then run `script` in the same shell."""
    return subprocess.run(
        ["/bin/bash", "-c", f"{SOURCE}; {script}"],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _call(func: str, *args: str) -> subprocess.CompletedProcess:
    """Source upgrade.sh with FASTAK_UPGRADE_LIB_ONLY=1 and call one function.

    `bash -c SCRIPT NAME ARG1 ARG2` binds NAME to $0, so the first real
    argument is $1 — not $2.
    """
    quoted = " ".join(f'"${i + 1}"' for i in range(len(args)))
    return subprocess.run(
        ["/bin/bash", "-c", f"{SOURCE}; {func} {quoted}", "_", *args],
        capture_output=True,
        text=True,
    )


# ── Version-gap detection ────────────────────────────────────────────────


@pytest.mark.parametrize("current,target", [("15", "18"), ("17", "18"), ("18", "19")])
def test_differing_majors_need_migration(current, target):
    assert _call("upgrade_needs_migration", current, target).returncode == 0


@pytest.mark.parametrize("major", ["15", "18"])
def test_same_major_needs_no_migration(major):
    assert _call("upgrade_needs_migration", major, major).returncode == 1


def test_absent_current_major_needs_no_migration():
    """No volume means a fresh install — nothing to migrate."""
    assert _call("upgrade_needs_migration", "", "18").returncode == 1


# ── Project-name derivation ──────────────────────────────────────────────
#
# This decides which volumes get deleted, so it is the highest-consequence
# computation in the script. It used to be `basename | tr -cd '[:alnum:]'`,
# which drops the `-` and `_` that Compose keeps: a repo in `Fast-TAK_Probe/`
# derived `fasttakprobe` while Compose had created `fast-tak_probe`. The name
# is now read straight out of `docker compose config --format json`, so these
# tests assert it survives verbatim rather than being re-normalised.


@pytest.mark.parametrize(
    "name",
    [
        "fasttak",  # plain
        "fast-tak",  # hyphen — dropped by the old tr
        "fast_tak",  # underscore — dropped by the old tr
        "fast-tak_probe",  # both
        "fasttak-wt-feat-tak-58",  # the repo's own worktree convention
        "fasttak-test-1234567890",  # the integration harness's project name
    ],
)
def test_project_name_is_taken_from_compose_verbatim(name):
    result = _bash("upgrade_project_name_from_config", stdin=json.dumps({"name": name}))
    assert result.returncode == 0
    assert result.stdout == name


def test_project_name_keeps_a_name_compose_already_lowercased():
    """Compose lowercases before emitting, so uppercase never reaches us.

    The guard that matters is that we do not re-transform it: whatever Compose
    says is the literal volume-name prefix it used.
    """
    result = _bash("upgrade_project_name_from_config", stdin=json.dumps({"name": "FastTAK"}))
    assert result.returncode == 0
    assert result.stdout == "FastTAK"


def test_project_name_with_leading_hyphen_stripped_by_compose():
    """A directory named `-weird` normalises to `weird` in compose-go.

    We pass through whatever it emits — including the already-stripped form —
    instead of running our own leading-character rule that could disagree.
    """
    result = _bash("upgrade_project_name_from_config", stdin=json.dumps({"name": "weird"}))
    assert result.returncode == 0
    assert result.stdout == "weird"


@pytest.mark.parametrize(
    "payload",
    [
        "",  # compose printed nothing
        "not json at all",
        json.dumps({}),  # no name key
        json.dumps({"name": ""}),  # empty name
        json.dumps({"name": None}),
        json.dumps(["fasttak"]),  # not an object
    ],
)
def test_project_name_failure_is_loud(payload):
    """Refuse to guess: a wrong project name deletes another stack's volume."""
    result = _bash("upgrade_project_name_from_config", stdin=payload)
    assert result.returncode != 0
    assert result.stdout == ""


# ── COMPOSE_ARGS construction ────────────────────────────────────────────


def test_compose_args_default_is_env_file_only():
    """Unset FASTAK_COMPOSE_FILES: compose auto-loads, we only pin the env."""
    result = _bash('upgrade_compose_args "/repo/.env" "${FASTAK_COMPOSE_FILES:-}"')
    assert result.returncode == 0
    assert result.stdout.split("\n")[:-1] == ["--env-file", "/repo/.env"]


def test_compose_args_splits_on_colons():
    result = _bash('upgrade_compose_args "/tmp/x/.env" "a.yml:b.yml:c.yml"')
    assert result.stdout.split("\n")[:-1] == [
        "-f",
        "a.yml",
        "-f",
        "b.yml",
        "-f",
        "c.yml",
        "--env-file",
        "/tmp/x/.env",
    ]


def test_compose_args_puts_env_file_last():
    """--env-file must not be swallowed as the value of a trailing -f."""
    out = _bash('upgrade_compose_args "/e/.env" "only.yml"').stdout.split("\n")[:-1]
    assert out[-2:] == ["--env-file", "/e/.env"]


def test_compose_args_restores_ifs():
    """The loop sets IFS=: to split; leaving it set would break later word splits."""
    result = _bash(
        'IFS="|"; upgrade_compose_args "/e/.env" "a.yml:b.yml" >/dev/null; printf "[%s]" "$IFS"'
    )
    assert result.stdout == "[|]"


def test_compose_args_never_empty_under_set_u():
    """A truly empty array trips `set -u` on bash 3.2 when expanded."""
    result = _bash(
        'set -u; COMPOSE_ARGS=(); while IFS= read -r a; do COMPOSE_ARGS+=("$a"); done '
        '< <(upgrade_compose_args "/e/.env" ""); printf "%s " "${COMPOSE_ARGS[@]}"'
    )
    assert result.returncode == 0
    assert result.stdout == "--env-file /e/.env "


# ── Argument parsing ─────────────────────────────────────────────────────


def test_parse_args_defaults_to_migrating_everything_with_a_prompt():
    result = _bash('upgrade_parse_args; printf "%s %s" "$SKIP_COT" "$ASSUME_YES"')
    assert result.returncode == 0
    assert result.stdout == "false false"


@pytest.mark.parametrize(
    "argv,expected",
    [
        ("--skip-cot", "true false"),
        ("--yes", "false true"),
        ("-y", "false true"),
        ("--skip-cot --yes", "true true"),
        ("--yes --skip-cot", "true true"),
    ],
)
def test_parse_args_flags(argv, expected):
    result = _bash(f'upgrade_parse_args {argv}; printf "%s %s" "$SKIP_COT" "$ASSUME_YES"')
    assert result.returncode == 0
    assert result.stdout == expected


def test_parse_args_rejects_unknown_argument():
    result = _bash("upgrade_parse_args --demolish")
    assert result.returncode == 2
    assert "Unknown argument: --demolish" in result.stderr


def test_unknown_argument_exits_2_when_executed():
    """The exit status, not just the function's return, has to be 2."""
    result = subprocess.run([str(UPGRADE), "--demolish"], capture_output=True, text=True)
    assert result.returncode == 2


# ── The gating truth table ───────────────────────────────────────────────
#
# upgrade_plan prints "<migrate-app-db> <migrate-cot> <nothing-to-migrate>".

APP_TARGET = "18"
COT_TARGET = "18"


@pytest.mark.parametrize(
    "app_current,cot_current,skip_cot,expected",
    [
        # Both stale, normal run: migrate both.
        ("15", "15", "false", "true true false"),
        # Both stale, --skip-cot: app-db still restored, cot discarded.
        ("15", "15", "true", "true false false"),
        # Only app-db stale.
        ("15", "18", "false", "true false false"),
        ("15", "18", "true", "true false false"),
        # Only cot stale.
        ("18", "15", "false", "false true false"),
        # ...and --skip-cot throws that stale history away rather than moving it.
        ("18", "15", "true", "false false false"),
        # Nothing stale: the early exit fires.
        ("18", "18", "false", "false false true"),
        # Fresh install (no volumes at all) is also "nothing to migrate".
        ("", "", "false", "false false true"),
        # But --skip-cot deliberately disables that exit: tak-db-data is
        # recreated unconditionally, which is the point of asking for it.
        ("18", "18", "true", "false false false"),
        ("", "", "true", "false false false"),
    ],
)
def test_plan_truth_table(app_current, cot_current, skip_cot, expected):
    result = _call("upgrade_plan", app_current, APP_TARGET, cot_current, COT_TARGET, skip_cot)
    assert result.returncode == 0
    assert result.stdout == expected


@pytest.mark.parametrize("app_current", ["15", "16", "17"])
def test_skip_cot_never_skips_the_app_db_restore(app_current):
    """--skip-cot is about CoT history only. Dropping app-db would lose users."""
    result = _call("upgrade_plan", app_current, APP_TARGET, "15", COT_TARGET, "true")
    assert result.stdout.split()[0] == "true"


# ── Archive identification ───────────────────────────────────────────────


def test_archive_name_comes_from_the_backup_command():
    out = "ok: fasttak-backup-20260814T120000Z-0.28.4.age (1234 bytes)\n"
    result = _bash("upgrade_archive_name_from_output", stdin=out)
    assert result.stdout.strip() == "fasttak-backup-20260814T120000Z-0.28.4.age"


def test_archive_name_takes_the_last_ok_line():
    out = (
        "ok: fasttak-backup-20260101T000000Z-0.1.0.age (1 bytes)\n"
        "ok: fasttak-backup-20260814T120000Z-0.28.4.age (2 bytes)\n"
    )
    result = _bash("upgrade_archive_name_from_output", stdin=out)
    assert result.stdout.strip() == "fasttak-backup-20260814T120000Z-0.28.4.age"


def test_archive_name_survives_crlf():
    out = "ok: fasttak-backup-20260814T120000Z-0.28.4.age (7 bytes)\r\n"
    result = _bash("upgrade_archive_name_from_output", stdin=out)
    assert result.stdout.strip() == "fasttak-backup-20260814T120000Z-0.28.4.age"


@pytest.mark.parametrize(
    "out",
    [
        "",
        "backup failed: RuntimeError: boom\n",
        "pruned: fasttak-backup-20260101T000000Z-0.1.0.age\n",
        "ok: something-else.tar.gz (5 bytes)\n",
    ],
)
def test_archive_name_absent_yields_nothing(out):
    """Empty output makes the caller fail loudly instead of guessing by mtime."""
    result = _bash("upgrade_archive_name_from_output", stdin=out)
    assert result.stdout.strip() == ""


# ── BACKUP_DIR normalisation ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "given,expected",
    [
        ("/srv/backups", "/srv/backups"),
        ("./backups", f"{REPO}/backups"),
        ("backups", f"{REPO}/backups"),
    ],
)
def test_backup_dir_is_made_absolute(given, expected):
    result = _call("upgrade_abs_path", given)
    assert result.stdout == expected
