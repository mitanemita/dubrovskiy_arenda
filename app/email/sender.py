"""Отправка писем через SMTP (aiosmtplib), с вложением PDF."""
from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings


async def send_email(
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    filename: str = "document.pdf",
) -> None:
    """Отправляет письмо; при наличии attachment прикрепляет PDF."""
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.email_login
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_server,
        port=settings.smtp_port,
        username=settings.email_login,
        password=settings.email_password,
        use_tls=settings.smtp_port == 465,
        start_tls=settings.smtp_port == 587,
    )
