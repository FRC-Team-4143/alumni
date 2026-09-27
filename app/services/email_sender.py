"""
Outbound email — the one channel this app otherwise avoided owning (see CLAUDE.md's
"BYO email" section: the newsletter itself is deliberately BYO, via a CSV export into
whatever mail-merge tool an admin already has). Self-service link recovery
(routers/portal.py's /survey/find) is different: a plain identical BCC blast has no way
to carry a personalized link, and a graduate who's lost Slack access has no other
channel back in, so this app has to be able to send *something* itself.

Plain `smtplib` over any SMTP-capable provider (a real mailbox, Gmail with an app
password, or a transactional API's SMTP endpoint) — no vendor SDK, matching this app's
"small team, no infra it doesn't need" posture. `smtplib` is blocking, so the actual
send runs in a worker thread via `anyio.to_thread` (already a FastAPI/Starlette
dependency — no new package).
"""
import logging
import smtplib
from email.message import EmailMessage

import anyio

from app.config import settings

log = logging.getLogger(__name__)


def _send_sync(to_address: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (
        f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
        if settings.smtp_from_name else settings.smtp_from_address
    )
    msg["To"] = to_address
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        if settings.smtp_use_tls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(msg)


async def send_email(to_address: str, subject: str, body: str) -> bool:
    """Best-effort send — never raises, matching every other outbound integration here
    (see services/slack_client.py). False (logged) if SMTP isn't configured or the send
    fails; the caller decides what, if anything, to tell the person who asked."""
    if not settings.smtp_host or not settings.smtp_from_address:
        log.warning("Cannot send email to %s: SMTP is not configured.", to_address)
        return False
    try:
        await anyio.to_thread.run_sync(_send_sync, to_address, subject, body)
        return True
    except Exception:
        log.exception("send_email failed for %s", to_address)
        return False
