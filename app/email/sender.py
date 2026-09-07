"""Отправка писем: SMTP или HTTPS email-API (Brevo/SendGrid/Resend).

Многие хостеры блокируют исходящий SMTP (порты 25/465/587). Тогда в .env задаётся
EMAIL_PROVIDER=brevo|sendgrid|resend + EMAIL_API_KEY + EMAIL_FROM, и письма уходят
по HTTPS (443), который обычно открыт. По умолчанию EMAIL_PROVIDER=smtp.

SMTP-транспорт принудительно использует IPv4 (у многих серверов нет IPv6-маршрута,
из-за чего smtp.gmail.com даёт «Network is unreachable»).
"""
from __future__ import annotations

import asyncio
import base64
import socket
from email.message import EmailMessage

import aiohttp
import aiosmtplib

from app.config import get_settings


# --- SMTP (IPv4) -----------------------------------------------------------
def _connect_ipv4_sock(host: str, port: int, timeout: float) -> socket.socket:
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


async def _send_smtp(settings, msg: EmailMessage, timeout: float) -> None:
    sock = await asyncio.to_thread(_connect_ipv4_sock, settings.smtp_server, settings.smtp_port, timeout)
    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_server,
        port=settings.smtp_port,
        username=settings.email_login,
        password=settings.email_password,
        use_tls=settings.smtp_port == 465,
        start_tls=settings.smtp_port == 587,
        timeout=timeout,
        sock=sock,
    )


# --- HTTPS email-API -------------------------------------------------------
async def _post_json(url: str, headers: dict, payload: dict, timeout: float, ok_statuses=(200, 201, 202)) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status not in ok_statuses:
                text = (await resp.text())[:500]
                raise RuntimeError(f"{url} → HTTP {resp.status}: {text}")


async def _send_brevo(settings, to, subject, body, attachment, filename, timeout) -> None:
    payload = {
        "sender": {"email": settings.sender_email},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    if attachment is not None:
        payload["attachment"] = [{"content": base64.b64encode(attachment).decode(), "name": filename}]
    await _post_json(
        "https://api.brevo.com/v3/smtp/email",
        {"api-key": settings.email_api_key, "accept": "application/json", "content-type": "application/json"},
        payload, timeout,
    )


async def _send_sendgrid(settings, to, subject, body, attachment, filename, timeout) -> None:
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": settings.sender_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    if attachment is not None:
        payload["attachments"] = [{
            "content": base64.b64encode(attachment).decode(),
            "filename": filename, "type": "application/pdf", "disposition": "attachment",
        }]
    await _post_json(
        "https://api.sendgrid.com/v3/mail/send",
        {"authorization": f"Bearer {settings.email_api_key}", "content-type": "application/json"},
        payload, timeout,
    )


async def _send_resend(settings, to, subject, body, attachment, filename, timeout) -> None:
    payload = {"from": settings.sender_email, "to": [to], "subject": subject, "text": body}
    if attachment is not None:
        payload["attachments"] = [{"filename": filename, "content": base64.b64encode(attachment).decode()}]
    await _post_json(
        "https://api.resend.com/emails",
        {"authorization": f"Bearer {settings.email_api_key}", "content-type": "application/json"},
        payload, timeout,
    )


_API_SENDERS = {"brevo": _send_brevo, "sendgrid": _send_sendgrid, "resend": _send_resend}


# --- Публичный интерфейс ---------------------------------------------------
async def send_email(
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    filename: str = "document.pdf",
    timeout: float = 20.0,
) -> None:
    """Отправляет письмо выбранным транспортом (EMAIL_PROVIDER)."""
    settings = get_settings()
    provider = (settings.email_provider or "smtp").lower()
    if provider in _API_SENDERS:
        await _API_SENDERS[provider](settings, to, subject, body, attachment, filename, timeout)
        return
    msg = EmailMessage()
    msg["From"] = settings.sender_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)
    await _send_smtp(settings, msg, timeout)


async def email_check(timeout: float = 15.0) -> str:
    """Проверяет доступность выбранного транспорта (без отправки письма)."""
    settings = get_settings()
    provider = (settings.email_provider or "smtp").lower()

    if provider in _API_SENDERS:
        checks = {
            "brevo": ("https://api.brevo.com/v3/account", {"api-key": settings.email_api_key}),
            "sendgrid": ("https://api.sendgrid.com/v3/scopes", {"authorization": f"Bearer {settings.email_api_key}"}),
            "resend": ("https://api.resend.com/domains", {"authorization": f"Bearer {settings.email_api_key}"}),
        }
        url, headers = checks[provider]
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status in (200, 201):
                    return f"{provider}: ключ принят, отправитель {settings.sender_email}"
                text = (await resp.text())[:300]
                raise RuntimeError(f"{provider} HTTP {resp.status}: {text}")

    # SMTP
    sock = await asyncio.to_thread(_connect_ipv4_sock, settings.smtp_server, settings.smtp_port, timeout)
    client = aiosmtplib.SMTP(
        hostname=settings.smtp_server, port=settings.smtp_port,
        use_tls=settings.smtp_port == 465, start_tls=settings.smtp_port == 587,
        timeout=timeout, sock=sock,
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
