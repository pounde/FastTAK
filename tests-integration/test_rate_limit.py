"""Rate limit integration tests.

Verifies that /auth/verify returns HTTP 429 with a Retry-After header
after the configured max attempts from a single source IP. Uses the
monitor container as the traffic source (it can reach ldap-proxy on
the Docker network at http://ldap-proxy:8080).
"""

import pytest

pytestmark = pytest.mark.integration


# One auth attempt from inside the monitor container. Returns the HTTP status code
# and the value of the Retry-After header (or empty string if absent).
AUTH_PROBE_SCRIPT = """
import urllib.request, base64
req = urllib.request.Request(
    'http://ldap-proxy:8080/auth/verify',
    headers={'Authorization': 'Basic ' + base64.b64encode(b'nonexistent:wrong-pw').decode()},
)
try:
    resp = urllib.request.urlopen(req, timeout=3)
    print(f'{resp.status}|{resp.getheader("Retry-After", "")}')
except urllib.error.HTTPError as e:
    print(f'{e.code}|{e.headers.get("Retry-After", "")}')
"""


def probe(compose_exec) -> tuple[int, str]:
    result = compose_exec("monitor", ["python3", "-c", AUTH_PROBE_SCRIPT])
    status_str, retry_after = result.stdout.strip().split("|", 1)
    return int(status_str), retry_after


class TestRateLimit:
    def test_429_after_budget_exhausted(self, compose_exec):
        """10 bad-auth attempts get 401; the 11th gets 429 with Retry-After.

        Defaults per DD-035: 10 attempts / 5 min window / 15 min lockout.
        After triggering the lockout, later tests are unaffected because they
        don't hit /auth/verify.
        """
        # First 10 attempts: within budget, should get 401 (bad credentials)
        for attempt_num in range(1, 11):
            status, _ = probe(compose_exec)
            assert status == 401, f"attempt {attempt_num}: expected 401, got {status}"

        # 11th attempt: over budget, should get 429 with Retry-After header
        status, retry_after = probe(compose_exec)
        assert status == 429, f"expected 429 on attempt 11, got {status}"
        assert retry_after != "", "expected Retry-After header on 429"
        retry_seconds = int(retry_after)
        # Lockout default is 15 minutes = 900 seconds. Allow some slack.
        assert 1 <= retry_seconds <= 900, f"Retry-After {retry_seconds} outside expected range"
