"""Every consumer reads .env through one reader.

scripts/lib-env.sh implements Compose's own dotenv semantics — notably
stripping surrounding quotes. `grep '^KEY=' .env | cut -d= -f2` does not, and
that difference is not cosmetic: with `DEPLOY_MODE="direct"` in .env,
scripts/upgrade.sh (env_get) selected docker-compose.direct.yml while start.sh
and the justfile's up/down recipes (grep | cut) saw `"direct"`, matched
nothing, and fell through to subdomain. caddy then came up without the Monitor,
Node-RED and MediaMTX port publishings — from a start, or an upgrade, that
reported success.

`cut -d= -f2` also truncates any value containing `=`, which every generated
secret can contain.

The callers that cannot source the library — a justfile recipe, and POSIX-sh
reconfig.sh — go through scripts/env-get.sh, the same library behind a
one-line CLI.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENV_GET = REPO / "scripts" / "env-get.sh"

# Every file that reads a value out of .env.
READERS = [
    REPO / "setup.sh",
    REPO / "start.sh",
    REPO / "reconfig.sh",
    REPO / "justfile",
    REPO / "scripts" / "check-env.sh",
    REPO / "scripts" / "ensure-secrets.sh",
    REPO / "scripts" / "upgrade.sh",
]

# `grep '^KEY=' <file> | cut -d= -f2` — the reader this replaced, in any of its
# spellings.
GREP_CUT = re.compile(r"""grep\s+['"]\^[A-Z_]+=['"].*\|\s*cut\s+-d=""")


def _code(path: Path) -> str:
    """The file's lines with whole-line comments dropped.

    The comments explain the defect and quote the old pipeline, so a raw
    substring search over the file would match its own explanation.
    """
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _get(env_file: Path, key: str) -> str:
    result = subprocess.run(
        ["/bin/bash", str(ENV_GET), str(env_file), key],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("path", READERS, ids=lambda p: p.name)
def test_no_second_env_reader(path):
    """The assertion that keeps the two readers from diverging again."""
    assert not GREP_CUT.search(_code(path)), f"{path.name} reads .env with grep | cut"


@pytest.mark.parametrize(
    "path",
    [
        REPO / "start.sh",
        REPO / "justfile",
        REPO / "reconfig.sh",
        REPO / "scripts" / "upgrade.sh",
    ],
    ids=lambda p: p.name,
)
def test_deploy_mode_comes_from_the_shared_reader(path):
    """These pick the compose files, or compare against them. Disagreeing about DEPLOY_MODE is how
    docker-compose.direct.yml goes missing from one of them."""
    code = _code(path)
    assert "DEPLOY_MODE" in code
    assert "env_get" in code or "env-get.sh" in code


@pytest.mark.parametrize(
    "line,expected",
    [
        ("DEPLOY_MODE=direct", "direct"),
        ('DEPLOY_MODE="direct"', "direct"),
        ("DEPLOY_MODE='direct'", "direct"),
        ("export DEPLOY_MODE=direct", "direct"),
        ("  DEPLOY_MODE=subdomain  ", "subdomain"),
        ("DEPLOY_MODE=", ""),
    ],
)
def test_env_get_cli_matches_compose_dotenv_semantics(tmp_path, line, expected):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{line}\n")
    assert _get(env_file, "DEPLOY_MODE") == expected


def test_env_get_cli_keeps_an_equals_sign_in_the_value(tmp_path):
    """`cut -d= -f2` truncated these — and generated secrets contain `=`."""
    env_file = tmp_path / ".env"
    env_file.write_text("TOKENS_API_SECRET=abc=def==\n")
    assert _get(env_file, "TOKENS_API_SECRET") == "abc=def=="


def test_env_get_cli_is_quiet_about_an_absent_key(tmp_path):
    """A recipe runs under `set -e` and has a documented default for an unset
    DEPLOY_MODE, so a lookup that fails the recipe would be the worse answer."""
    env_file = tmp_path / ".env"
    env_file.write_text("SERVER_ADDRESS=tak.internal\n")
    assert _get(env_file, "DEPLOY_MODE") == ""


def test_env_get_cli_is_quiet_about_an_absent_file(tmp_path):
    assert _get(tmp_path / "nope.env", "DEPLOY_MODE") == ""


def test_env_get_cli_rejects_the_wrong_argument_count():
    result = subprocess.run(
        ["/bin/bash", str(ENV_GET), "only-one"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
