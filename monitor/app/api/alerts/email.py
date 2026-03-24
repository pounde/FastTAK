"""Send alert emails via SMTP."""

import smtplib
from email.mime.text import MIMEText

from app.config import settings


def send_alert_email(subject: str, body: str) -> bool:
    """Send an alert email. Returns True on success."""
    if not all([settings.smtp_host, settings.alert_email]):
        return False

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"[FastTAK] {subject}"
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = settings.alert_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        return False
