# tests/test_upgrade_planning.py
"""Planning helpers in scripts/upgrade.sh.

Everything here runs with FASTAK_UPGRADE_LIB_ONLY=1, which sources the script's
helper definitions and returns before it touches Docker. The migration itself
moves Docker volumes; that destructive path is covered by
tests-integration/test_upgrade_rehearsal.py, which rehearses `just upgrade`
against a live stack and asserts row counts survive the restore.
"""

import json
import os
import shlex
import subprocess
import textwrap
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


# ── Compose file selection (DEPLOY_MODE) ─────────────────────────────────
#
# docker-compose.direct.yml is the only place caddy publishes 1880 / 8180 /
# 8888; base compose publishes 80/443 alone. This script ends with
# `compose up -d --build --remove-orphans`, so leaving the overlay out recreates
# caddy without those ports — the Monitor, Node-RED and MediaMTX unreachable
# off-host after an upgrade that printed "Upgrade Complete". start.sh and the
# justfile's up/down recipes all select the overlay the same way.


def _compose_files(mode: str, explicit: str = "", repo: str = "/repo"):
    return _call("upgrade_compose_files", mode, explicit, repo)


def test_subdomain_mode_leaves_compose_to_auto_load():
    """The default: docker-compose.yml plus docker-compose.override.yml, which
    compose loads on its own. Naming them explicitly would gain nothing."""
    result = _compose_files("subdomain")
    assert result.returncode == 0
    assert result.stdout == ""


def test_direct_mode_selects_the_direct_overlay(tmp_path):
    result = _compose_files("direct", "", str(tmp_path))
    assert result.returncode == 0
    assert result.stdout == "docker-compose.yml:docker-compose.direct.yml"


def test_direct_mode_reappends_an_existing_override_last(tmp_path):
    """An explicit file list disables compose's override auto-load, so the
    operator's override has to be named again — and last, so it still wins."""
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
    result = _compose_files("direct", "", str(tmp_path))
    assert result.stdout == (
        "docker-compose.yml:docker-compose.direct.yml:docker-compose.override.yml"
    )


@pytest.mark.parametrize("mode", ["subdomain", "direct", ""])
def test_explicit_compose_files_win_over_deploy_mode(mode, tmp_path):
    """FASTAK_COMPOSE_FILES — which the rehearsal integration test sets to an
    isolated stack — names every file that run needs. DEPLOY_MODE must not add
    the direct overlay's production port bindings to it."""
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
    result = _compose_files(mode, "/tmp/t/docker-compose.yml:/tmp/t/test.yml", str(tmp_path))
    assert result.returncode == 0
    assert result.stdout == "/tmp/t/docker-compose.yml:/tmp/t/test.yml"


def test_direct_mode_reaches_compose_args_as_f_flags(tmp_path):
    """End to end through both helpers: the -f flags precede --env-file."""
    result = _bash(
        'upgrade_compose_args "/repo/.env" '
        f'"$(upgrade_compose_files direct "" {shlex.quote(str(tmp_path))})"'
    )
    assert result.stdout.split("\n")[:-1] == [
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.direct.yml",
        "--env-file",
        "/repo/.env",
    ]


# ── The whole script, with Docker stubbed ────────────────────────────────
#
# The compose arguments are built outside the FASTAK_UPGRADE_LIB_ONLY section,
# so the helpers above cannot show that the script actually uses them. These
# run scripts/upgrade.sh for real against a stub `docker` that records every
# invocation and reports both volumes absent — which reaches "nothing to
# migrate" and exits 0 without touching anything.


def _stub_docker(tmp_path):
    """A `docker` that logs its argv and answers the read-only probes."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    log = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        'case "$*" in\n'
        "  info) exit 0 ;;\n"
        '  *"config --format json"*) printf \'{"name":"fasttak"}\' ;;\n'
        '  *"volume inspect"*) exit 1 ;;\n'  # absent: a fresh install
        "esac\n"
        "exit 0\n"
    )
    docker.chmod(0o755)
    # Only its presence is checked, in the preflight loop.
    age = bin_dir / "age"
    age.write_text("#!/bin/sh\nexit 0\n")
    age.chmod(0o755)
    return bin_dir, log


def _env_file(tmp_path, deploy_mode):
    """The minimum scripts/check-env.sh accepts."""
    path = tmp_path / "test.env"
    path.write_text(
        "SERVER_ADDRESS=tak.test.invalid\n"
        "TAK_VERSION=5.8-RELEASE-65\n"
        "TAK_WEBADMIN_PASSWORD=not-the-documented-default\n"
        "TOKENS_API_SECRET=0123456789abcdef\n"
        f"DEPLOY_MODE={deploy_mode}\n"
        f"BACKUP_DIR={tmp_path}/backups\n"
    )
    return path


def _run_upgrade(tmp_path, deploy_mode, **env):
    bin_dir, log = _stub_docker(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(UPGRADE), "--yes"],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FASTAK_ENV_FILE": str(_env_file(tmp_path, deploy_mode)),
            **env,
        },
    )
    return result, log.read_text() if log.exists() else ""


def test_direct_mode_runs_compose_with_the_direct_overlay(tmp_path):
    """The regression: upgrade.sh passed --env-file and nothing else, so
    compose auto-loaded base + override and dropped docker-compose.direct.yml
    — the only file publishing caddy's 1880 / 8180 / 8888. The final
    `compose up -d` then recreated caddy without them and printed
    "Upgrade Complete"."""
    result, calls = _run_upgrade(tmp_path, "direct")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Nothing to migrate" in result.stdout
    compose_calls = [c for c in calls.splitlines() if c.startswith("compose ")]
    assert compose_calls, calls
    for call in compose_calls:
        assert "-f docker-compose.yml" in call
        assert "-f docker-compose.direct.yml" in call
        assert "--env-file" in call


def test_subdomain_mode_leaves_compose_to_auto_load_end_to_end(tmp_path):
    result, calls = _run_upgrade(tmp_path, "subdomain")
    assert result.returncode == 0, result.stdout + result.stderr
    compose_calls = [c for c in calls.splitlines() if c.startswith("compose ")]
    assert compose_calls, calls
    for call in compose_calls:
        assert "-f " not in call
        assert "--env-file" in call


def test_explicit_compose_files_still_win_end_to_end(tmp_path):
    """The rehearsal integration test sets FASTAK_COMPOSE_FILES; DEPLOY_MODE
    must not add the direct overlay's production port bindings to it."""
    result, calls = _run_upgrade(
        tmp_path, "direct", FASTAK_COMPOSE_FILES="/t/docker-compose.yml:/t/test.yml"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    compose_calls = [c for c in calls.splitlines() if c.startswith("compose ")]
    assert compose_calls, calls
    for call in compose_calls:
        assert "-f /t/docker-compose.yml -f /t/test.yml" in call
        assert "docker-compose.direct.yml" not in call


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


# ── The cot invariant ────────────────────────────────────────────────────
#
#   restore_cot  ⟺  (the tak-db-data volume was removed)  ∧  ¬SKIP_COT
#
# The restore has to follow from what the script actually destroyed, never
# from a version comparison. tak-db-data is recreated whenever it exists —
# `docker volume rm` does not care whether the major changed — so gating the
# restore on "the cot major is stale" silently drops the whole CoT history
# every time tak-database is *already* on the target major. That is the
# permanent steady state after 5.8: the next app-db major bump would delete
# tak-db-data, restore nothing, and print "CoT history: migrated".
#
# upgrade_cot_plan prints "<remove-volume> <restore-cot>".

COT_STATES = ["15", "18", "empty", "absent"]


@pytest.mark.parametrize(
    "state,skip_cot,expected",
    [
        # Stale major: the volume goes, the history comes back.
        ("15", "false", "true true"),
        # NEW-3. tak-database already on the target major. The volume is still
        # removed, so the history still has to be restored.
        ("18", "false", "true true"),
        # The pre-5.8 shape: the volume exists but TAK 5.6 ran initdb into
        # /var/lib/postgresql/15/data, so it holds no data directory. The live
        # cot rows are in the container's writable layer, `compose down`
        # destroys them, and the archive's cot.sql is the only copy — restore.
        ("empty", "false", "true true"),
        # No volume at all: nothing is removed, so nothing is restored.
        ("absent", "false", "false false"),
        # --skip-cot suppresses the restore in every shape, never the removal.
        ("15", "true", "true false"),
        ("18", "true", "true false"),
        ("empty", "true", "true false"),
        ("absent", "true", "false false"),
    ],
)
def test_cot_plan_truth_table(state, skip_cot, expected):
    result = _call("upgrade_cot_plan", state, skip_cot)
    assert result.returncode == 0
    assert result.stdout == expected


@pytest.mark.parametrize("state", COT_STATES)
def test_cot_restore_iff_volume_removed_and_not_skipped(state):
    """The invariant itself, asserted rather than enumerated."""
    for skip_cot in ("false", "true"):
        removed, restore = _call("upgrade_cot_plan", state, skip_cot).stdout.split()
        assert restore == ("true" if removed == "true" and skip_cot == "false" else "false")


@pytest.mark.parametrize("state", COT_STATES)
def test_skip_cot_never_changes_what_is_removed(state):
    """--skip-cot skips the restore, not the destruction. Say so honestly."""
    without = _call("upgrade_cot_plan", state, "false").stdout.split()[0]
    with_ = _call("upgrade_cot_plan", state, "true").stdout.split()[0]
    assert without == with_


# ── The summary line ─────────────────────────────────────────────────────
#
# upgrade_cot_summary <cot-restored> <skip-cot>. "migrated" is a claim about
# rows that exist, so only a restore that actually ran and succeeded earns it.


def test_cot_summary_claims_migration_only_after_a_restore():
    result = _call("upgrade_cot_summary", "true", "false")
    assert result.stdout.strip() == "CoT history: migrated"


@pytest.mark.parametrize(
    "restored,skip_cot,expected",
    [
        ("false", "true", "CoT history: DISCARDED (--skip-cot)"),
        ("false", "false", "CoT history: unaffected (tak-db-data was not recreated)"),
        # --skip-cot wins the wording even if a restore somehow ran.
        ("true", "false", "CoT history: migrated"),
    ],
)
def test_cot_summary_wording(restored, skip_cot, expected):
    assert _call("upgrade_cot_summary", restored, skip_cot).stdout.strip() == expected


@pytest.mark.parametrize("skip_cot", ["false", "true"])
def test_cot_summary_never_says_migrated_without_a_restore(skip_cot):
    """The NEW-3 lie: data deleted, operator told it was carried across."""
    result = _call("upgrade_cot_summary", "false", skip_cot)
    assert "migrated" not in result.stdout


# ── The three-way volume probe ───────────────────────────────────────────
#
# `docker` is stubbed on PATH, so none of this needs a daemon. The three
# outcomes must stay separable: an existing-but-empty tak-db-data IS the
# pre-5.8 state this whole upgrade exists to fix, so reading it as a probe
# failure aborts the one upgrade the script is for.

PROBE_OK = 0
PROBE_ABSENT = 1
PROBE_NO_DATA = 2
PROBE_FAILED = 3


def _docker_stub(tmp_path, *, volume_exists=True, run_rc=0, run_stdout="", argv_log=None):
    """Write a fake `docker` and return the directory to prepend to PATH."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    log = f'printf "%s\\n" "$*" >> {shlex.quote(str(argv_log))}' if argv_log else ":"
    (bin_dir / "docker").write_text(
        textwrap.dedent(f"""\
        #!/bin/bash
        {log}
        case "${{1:-}} ${{2:-}}" in
          "volume inspect") exit {0 if volume_exists else 1} ;;
        esac
        case "${{1:-}}" in
          run) printf '%s\\n' {shlex.quote(run_stdout)}; exit {run_rc} ;;
        esac
        exit 0
        """)
    )
    (bin_dir / "docker").chmod(0o755)
    return bin_dir


def _probe(tmp_path, **kwargs):
    bin_dir = _docker_stub(tmp_path, **kwargs)
    return _bash(f'PATH={shlex.quote(str(bin_dir))}:"$PATH"; upgrade_volume_pg_major some-volume')


@pytest.mark.parametrize("major", ["15", "17", "18"])
def test_probe_reports_the_major_it_read(tmp_path, major):
    result = _probe(tmp_path, run_stdout=major)
    assert result.returncode == PROBE_OK
    assert result.stdout == major


def test_probe_strips_the_trailing_newline_pg_version_carries(tmp_path):
    assert _probe(tmp_path, run_stdout="15\n\n").stdout == "15"


def test_probe_reports_an_absent_volume_as_absent(tmp_path):
    """Fresh install. Not an error, and nothing to preserve."""
    result = _probe(tmp_path, volume_exists=False)
    assert result.returncode == PROBE_ABSENT
    assert result.stdout == ""


def test_probe_reports_a_volume_without_pg_version_as_no_data(tmp_path):
    """The pre-5.8 shape: volume present, empty. Must not abort the upgrade."""
    result = _probe(tmp_path, run_stdout="__NO_PG_VERSION__")
    assert result.returncode == PROBE_NO_DATA
    assert result.stdout == ""


def test_probe_reports_a_docker_failure_as_a_failure(tmp_path):
    """Daemon unreachable, or postgres:18-alpine could not be pulled."""
    assert _probe(tmp_path, run_rc=125).returncode == PROBE_FAILED


def test_probe_treats_silent_success_as_a_failure(tmp_path):
    """No version and no sentinel means the probe did not really answer."""
    assert _probe(tmp_path, run_stdout="").returncode == PROBE_FAILED


def test_probe_mounts_the_volume_read_only(tmp_path):
    """A probe that could write to the volume it is inspecting is a hazard."""
    log = tmp_path / "argv.log"
    _probe(tmp_path, run_stdout="15", argv_log=log)
    assert "some-volume:/v:ro" in log.read_text()


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
