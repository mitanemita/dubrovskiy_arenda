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
    timeout: float = 20.0,
) -> None:
    """Отправляет письмо; при наличии attachment прикрепляет PDF.

    timeout — ограничение на соединение/операции SMTP, чтобы не «висеть» долго,
    если сервер недоступен (часто провайдер блокирует исходящий SMTP).
    """
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
        timeout=timeout,
    )


async def smtp_check(timeout: float = 15.0) -> str:
    """Проверяет доступность SMTP и логин (без отправки письма). Возвращает описание.

    Бросает исключение с понятной причиной, если соединение/логин не удались.
    """
    settings = get_settings()
    client = aiosmtplib.SMTP(
        hostname=settings.smtp_server,
        port=settings.smtp_port,
        use_tls=settings.smtp_port == 465,
        start_tls=settings.smtp_port == 587,
        timeout=timeout,
    )
    await client.connect()
    try:
        if settings.email_login and settings.email_password:
            await client.login(settings.email_login, settings.email_password)
            state = "соединение и логин успешны"
        else:
            state = "соединение успешно (логин/пароль не заданы)"
    finally:
        try:
            await client.quit()
        except Exception:
            pass
    return f"{settings.smtp_server}:{settings.smtp_port} — {state}"
