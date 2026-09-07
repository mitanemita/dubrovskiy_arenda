"""Отправка писем через SMTP (aiosmtplib), с вложением PDF.

Соединение принудительно по IPv4: у многих серверов нет IPv6-маршрута, и тогда
`smtp.gmail.com` (который резолвится в IPv6) даёт «Network is unreachable».
Мы сами резолвим A-запись и подключаемся по IPv4, а имя хоста передаём для TLS.
"""
from __future__ import annotations

import asyncio
import socket
from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings


def _connect_ipv4_sock(host: str, port: int, timeout: float) -> socket.socket:
    """Блокирующе подключает TCP-сокет по IPv4 (вызывать через asyncio.to_thread)."""
    last_err: Exception | None = None
    for family, stype, proto, _canon, sa in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        s = socket.socket(family, stype, proto)
        s.settimeout(timeout)
        try:
            s.connect(sa)
            return s
        except OSError as exc:  # noqa: PERF203
            last_err = exc
            s.close()
    raise last_err or OSError(f"Не удалось подключиться к {host}:{port} по IPv4")


async def _open_ipv4_sock(host: str, port: int, timeout: float) -> socket.socket:
    return await asyncio.to_thread(_connect_ipv4_sock, host, port, timeout)


async def send_email(
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    filename: str = "document.pdf",
    timeout: float = 20.0,
) -> None:
    """Отправляет письмо (по IPv4); при наличии attachment прикрепляет PDF."""
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.email_login
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)

    sock = await _open_ipv4_sock(settings.smtp_server, settings.smtp_port, timeout)
    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_server,      # для TLS/SNI и проверки сертификата
        port=settings.smtp_port,
        username=settings.email_login,
        password=settings.email_password,
        use_tls=settings.smtp_port == 465,
        start_tls=settings.smtp_port == 587,
        timeout=timeout,
        sock=sock,
    )


async def smtp_check(timeout: float = 15.0) -> str:
    """Проверяет доступность SMTP (по IPv4) и логин, без отправки письма."""
    settings = get_settings()
    sock = await _open_ipv4_sock(settings.smtp_server, settings.smtp_port, timeout)
    client = aiosmtplib.SMTP(
        hostname=settings.smtp_server,
        port=settings.smtp_port,
        use_tls=settings.smtp_port == 465,
        start_tls=settings.smtp_port == 587,
        timeout=timeout,
        sock=sock,
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
    return f"{settings.smtp_server}:{settings.smtp_port} (IPv4) — {state}"
