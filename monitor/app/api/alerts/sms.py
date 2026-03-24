"""Send SMS alerts via Twilio or Brevo."""

import httpx

from app.config import settings


async def send_alert_sms(message: str) -> bool:
    """Send SMS to configured numbers. Returns True on success."""
    if not settings.sms_provider or not settings.sms_to:
        return False

    numbers = [n.strip() for n in settings.sms_to.split(",") if n.strip()]

    if settings.sms_provider == "twilio":
        return await _send_twilio(message, numbers)
    elif settings.sms_provider == "brevo":
        return await _send_brevo(message, numbers)
    return False


async def _send_twilio(message: str, numbers: list[str]) -> bool:
    # sms_api_key format: "account_sid:auth_token"
    parts = settings.sms_api_key.split(":", 1)
    if len(parts) != 2:
        return False
    account_sid, auth_token = parts
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    success = True
    async with httpx.AsyncClient(timeout=10) as client:
        for number in numbers:
            resp = await client.post(
                url,
                auth=(account_sid, auth_token),
                data={
                    "From": settings.sms_from,
                    "To": number,
                    "Body": message[:1600],
                },
            )
            if resp.status_code >= 400:
                success = False
    return success


async def _send_brevo(message: str, numbers: list[str]) -> bool:
    url = "https://api.brevo.com/v3/transactionalSMS/sms"
    headers = {"api-key": settings.sms_api_key, "Content-Type": "application/json"}

    success = True
    async with httpx.AsyncClient(timeout=10) as client:
        for number in numbers:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "type": "transactional",
                    "sender": settings.sms_from[:11],
                    "recipient": number,
                    "content": message[:1600],
                },
            )
            if resp.status_code >= 400:
                success = False
    return success
