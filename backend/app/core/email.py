"""Outgoing email.

Backends (``EMAIL_BACKEND``):
- ``smtp``: real delivery. In development this is Mailpit (docker compose), which
  catches every message; read them at http://127.0.0.1:8025.
- ``console``: logs messages instead of sending. Development only: the log then
  contains invitation links.
"""

import logging
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class SmtpEmailSender:
    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = settings.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        if message.html:
            mime.add_alternative(message.html, subtype="html")
        await aiosmtplib.send(
            mime,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            start_tls=settings.smtp_starttls,
            timeout=10,
        )


class ConsoleEmailSender:
    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "Email (console backend)\nTo: %s\nSubject: %s\n\n%s",
            message.to,
            message.subject,
            message.text,
        )


def get_email_sender() -> EmailSender:
    return SmtpEmailSender() if settings.email_backend == "smtp" else ConsoleEmailSender()


async def send_email_safely(sender: EmailSender, message: EmailMessage) -> None:
    """For background tasks: delivery failures are logged, never raised."""
    try:
        await sender.send(message)
    except Exception:
        logger.exception("Failed to send email %r", message.subject)
