"""Send SMS alerts via Twilio or Brevo."""

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def send_alert_sms(message: str) -> bool:
    """Send SMS to configured numbers. Returns True only if every send succeeded.

    Never raises: a provider outage during a critical alert must not throw
    out of the scheduler's poll tick (#58).
    """
    if not settings.sms_provider or not settings.sms_to:
        return False

    numbers = [n.strip() for n in settings.sms_to.split(",") if n.strip()]

    if settings.sms_provider == "twilio":
        return _send_twilio(message, numbers)
    elif settings.sms_provider == "brevo":
        return _send_brevo(message, numbers)
    return False


def _post(client: httpx.Client, number: str, **request) -> bool:
    """One provider call. False on a transport error or a 4xx/5xx.

    Catches Exception, not just httpx.HTTPError: httpx.InvalidURL (e.g. from
    a malformed account SID) is not an HTTPError subclass, and this function
    must never raise out of the scheduler's poll tick (#58).
    """
    try:
        resp = client.post(**request)
    except Exception as exc:
        log.warning("SMS to %s failed: %s: %s", number, type(exc).__name__, exc)
        return False
    if resp.status_code >= 400:
        # Neither provider echoes credentials in error bodies; the message
        # body is ours, so logging a slice of the response is safe.
        log.warning("SMS to %s rejected: HTTP %s: %s", number, resp.status_code, resp.text[:200])
        return False
    return True


def _send_twilio(message: str, numbers: list[str]) -> bool:
    # sms_api_key format: "account_sid:auth_token"
    parts = settings.sms_api_key.split(":", 1)
    if len(parts) != 2:
        return False
    account_sid, auth_token = parts
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    success = True
    with httpx.Client(timeout=10) as client:
        for number in numbers:
            if not _post(
                client,
                number,
                url=url,
                auth=(account_sid, auth_token),
                data={"From": settings.sms_from, "To": number, "Body": message[:1600]},
            ):
                success = False
    return success


def _send_brevo(message: str, numbers: list[str]) -> bool:
    url = "https://api.brevo.com/v3/transactionalSMS/sms"
    headers = {"api-key": settings.sms_api_key, "Content-Type": "application/json"}

    success = True
    with httpx.Client(timeout=10) as client:
        for number in numbers:
            if not _post(
                client,
                number,
                url=url,
                headers=headers,
                json={
                    "type": "transactional",
                    "sender": settings.sms_from[:11],
                    "recipient": number,
                    "content": message[:1600],
                },
            ):
                success = False
    return success
