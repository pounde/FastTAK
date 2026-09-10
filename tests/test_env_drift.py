"""Nothing tells an operator when a release adds a setting, and nothing
tells a contributor when compose starts needing one that .env.example does
not document. Both halves, as set differences.

The repo-side half runs here with no deployment. The operator-side half is
`just check`, tested through check-env.sh --report.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB_ENV = REPO / "scripts" / "lib-env.sh"
CHECK = REPO / "scripts" / "check-env.sh"

# Interpolated by compose but exported by a script rather than set in .env.
SCRIPT_EXPORTED = {
    "FASTTAK_VERSION",
    "FASTTAK_COMMIT",  # scripts/lib-stack.sh
    "TAK_HOST_PATH",
    "HOST_ENV_FILE",
    "BACKUP_DIR",  # test-stack.sh
    "CAPTURE_DIR",
    "CAPTURE_CERT_DIR",  # docker-compose.capture.yml, with defaults
}

# Illustrative lines in .env.example, not settings: the FASTAK_MON_ prefix is
# an open-ended override convention and the specific keys are examples.
ILLUSTRATIVE_PREFIX = "FASTAK_MON_"

# Directories excluded from the "every documented key is used somewhere"
# haystack: not source that reads .env keys.
EXCLUDED_DIRS = ("tests/", "tests-integration/", "docs/")


def env_keys(path: Path) -> set[str]:
    out = subprocess.run(
        ["/bin/bash", "-c", f'. "{LIB_ENV}"; env_keys "{path}"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return set(out.split())


def compose_interpolations() -> dict[str, bool]:
    """name → has_default, over every docker-compose*.yml."""
    seen: dict[str, bool] = {}
    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(:?[-?][^}]*)?\}")
    for f in REPO.glob("docker-compose*.yml"):
        for name, default in pattern.findall(f.read_text()):
            seen[name] = seen.get(name, False) or bool(default)
    return seen


def test_every_required_compose_variable_is_documented():
    documented = env_keys(REPO / ".env.example") | SCRIPT_EXPORTED
    required = {n for n, has_default in compose_interpolations().items() if not has_default}
    missing = sorted(required - documented)
    assert not missing, f"compose needs these and .env.example does not document them: {missing}"


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_every_documented_key_is_used_somewhere():
    """The haystack is every tracked file, not a narrow glob list — a key used
    only in a Caddy template or in tak-database/*.sh would otherwise be a
    false failure. Excludes tests/docs (not consumers) and .env* files
    themselves (that would make a key "used" by merely being documented)."""
    keys = {k for k in env_keys(REPO / ".env.example") if not k.startswith(ILLUSTRATIVE_PREFIX)}
    haystack = ""
    for rel in _tracked_files():
        if any(rel.startswith(d) for d in EXCLUDED_DIRS):
            continue
        if Path(rel).name.startswith(".env"):
            continue
        path = REPO / rel
        try:
            haystack += path.read_text(errors="replace")
        except OSError:
            continue
    unused = sorted(k for k in keys if k not in haystack)
    assert not unused, f".env.example documents keys nothing reads: {unused}"


def _report(tmp_path, env_content: str) -> subprocess.CompletedProcess:
    env = tmp_path / ".env"
    env.write_text(env_content)
    return subprocess.run(
        ["/bin/bash", str(CHECK), "--report", str(env)], capture_output=True, text=True
    )


def test_report_never_fails(tmp_path):
    assert _report(tmp_path, "").returncode == 0


def test_report_lists_keys_new_in_this_release(tmp_path):
    result = _report(tmp_path, "SERVER_ADDRESS=x\n")
    assert result.returncode == 0
    assert "TOKENS_API_SECRET" in result.stdout


def test_report_lists_keys_this_release_does_not_use(tmp_path):
    example = (REPO / ".env.example").read_text()
    result = _report(tmp_path, example + "MY_OWN_THING=1\n")
    assert "MY_OWN_THING" in result.stdout
    assert "safe to remove" in result.stdout


def test_report_counts_commented_keys_as_present(tmp_path):
    """setup.sh creates .env as a copy of .env.example, so a fresh install has
    every key, most of them commented. Those must not read as missing."""
    example = (REPO / ".env.example").read_text()
    result = _report(tmp_path, example)
    assert "no drift" in result.stdout.lower()


def test_report_ignores_illustrative_keys(tmp_path):
    example = (REPO / ".env.example").read_text()
    stripped = (
        "\n".join(line for line in example.splitlines() if ILLUSTRATIVE_PREFIX not in line) + "\n"
    )
    result = _report(tmp_path, stripped)
    assert ILLUSTRATIVE_PREFIX not in result.stdout
