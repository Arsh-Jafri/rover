"""Resend-based email sender for Rover notifications."""

import os

import resend

from rover.logger import get_logger

logger = get_logger("emailer")


def send(to: str, subject: str, html: str, from_email: str, from_name: str = "Rover") -> dict:
    """Send an HTML email via Resend.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        html: HTML body content.
        from_email: Sender email (e.g. rover@tryrover.app).
        from_name: Display name for sender.

    Returns:
        Resend API response dict with 'id' key.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set in environment")

    resend.api_key = api_key

    params: resend.Emails.SendParams = {
        "from": f"{from_name} <{from_email}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }

    result = resend.Emails.send(params)
    logger.info("Email sent via Resend to %s (id=%s)", to, result.get("id"))
    return result
