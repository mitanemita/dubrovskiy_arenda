"""Отправка писем: SMTP, HTTPS email-API (Brevo/SendGrid/Resend), Gmail API или n8n.

Многие хостеры блокируют исходящий SMTP (порты 25/465/587). Тогда в .env задаётся
другой транспорт (EMAIL_PROVIDER), работающий по HTTPS/HTTP:
  smtp (по умолчанию) | n8n | gmail | brevo | sendgrid | resend

n8n-транспорт: бот POST-ит письмо (адресат, тема, текст, вложения-PDF и метаданные
документа) в вебхук n8n, а n8n собирает и отправляет письмо своей нодой (Gmail,
SMTP-relay и т.п.). Так вся отправка документов (УПД по аренде/электричеству и
квитанции) идёт единообразно через n8n на том же сервере. Формат payload — см.
docs/n8n_email.md и app/email/n8n_send_email_smtp.workflow.json
(вариант по HTTPS/API — app/email/n8n_send_email_brevo.workflow.json).

SMTP-транспорт принудительно использует IPv4 (у многих серверов нет IPv6-маршрута,
из-за чего smtp.gmail.com даёт «Network is unreachable»).
"""
from __future__ import annotations

import asyncio
import base64
import socket
from dataclasses import dataclass, field
from email.message import EmailMessage

import aiohttp
import aiosmtplib

from app.config import get_settings


# --- Нормализованное письмо ------------------------------------------------
@dataclass
class OutgoingEmail:
    """Единая модель письма для всех транспортов.

    attachments — список (имя файла, содержимое-байты). meta — произвольные
    метаданные документа (тип, арендатор, договор, период, сумма), которые
    транспорт n8n прокидывает в вебхук для гибкой сборки письма.
    """

    to: str
    subject: str
    body: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)
    meta: dict | None = None

    def as_email_message(self, sender: str) -> EmailMessage:
        """Собирает MIME-сообщение (для SMTP и Gmail API)."""
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = self.to
        msg["Subject"] = self.subject
        msg.set_content(self.body)
        for filename, content in self.attachments:
            msg.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
        return msg

    def attachments_b64(self) -> list[dict]:
        """Вложения в виде списка словарей с base64 (для JSON-транспортов)."""
        return [
            {
                "filename": filename,
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(content).decode(),
            }
            for filename, content in self.attachments
        ]


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


async def _send_smtp(settings, email: OutgoingEmail, timeout: float) -> None:
    msg = email.as_email_message(settings.sender_email)
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


async def _send_brevo(settings, email: OutgoingEmail, timeout: float) -> None:
    payload = {
        "sender": {"email": settings.sender_email},
        "to": [{"email": email.to}],
        "subject": email.subject,
        "textContent": email.body,
    }
    atts = email.attachments_b64()
    if atts:
        payload["attachment"] = [{"content": a["content_base64"], "name": a["filename"]} for a in atts]
    await _post_json(
        "https://api.brevo.com/v3/smtp/email",
        {"api-key": settings.email_api_key, "accept": "application/json", "content-type": "application/json"},
        payload, timeout,
    )


async def _send_sendgrid(settings, email: OutgoingEmail, timeout: float) -> None:
    payload = {
        "personalizations": [{"to": [{"email": email.to}]}],
        "from": {"email": settings.sender_email},
        "subject": email.subject,
        "content": [{"type": "text/plain", "value": email.body}],
    }
    atts = email.attachments_b64()
    if atts:
        payload["attachments"] = [{
            "content": a["content_base64"], "filename": a["filename"],
            "type": "application/pdf", "disposition": "attachment",
        } for a in atts]
    await _post_json(
        "https://api.sendgrid.com/v3/mail/send",
        {"authorization": f"Bearer {settings.email_api_key}", "content-type": "application/json"},
        payload, timeout,
    )


async def _send_resend(settings, email: OutgoingEmail, timeout: float) -> None:
    payload = {"from": settings.sender_email, "to": [email.to], "subject": email.subject, "text": email.body}
    atts = email.attachments_b64()
    if atts:
        payload["attachments"] = [{"filename": a["filename"], "content": a["content_base64"]} for a in atts]
    await _post_json(
        "https://api.resend.com/emails",
        {"authorization": f"Bearer {settings.email_api_key}", "content-type": "application/json"},
        payload, timeout,
    )


async def _send_n8n(settings, email: OutgoingEmail, timeout: float) -> None:
    """Отправка через вебхук n8n: n8n сам шлёт письмо (напр. Gmail-нодой по 443).

    Payload самодостаточен: n8n получает адресата, тему, текст, отправителя,
    метаданные документа и все вложения (base64). Для простых воркфлоу продублированы
    поля первого вложения (filename/pdf_base64). Если задан WEBHOOK_TOKEN — уходит
    в заголовке X-Webhook-Token, чтобы вебхук мог проверить источник.
    """
    if not settings.email_n8n_url:
        raise RuntimeError("EMAIL_N8N_URL не задан")
    atts = email.attachments_b64()
    payload = {
        "to": email.to,
        "subject": email.subject,
        "text": email.body,
        "from": settings.sender_email or None,
        "meta": email.meta or {},
        "attachments": atts,
        # legacy-поля первого вложения — для простых воркфлоу «одно письмо, один PDF»
        "filename": atts[0]["filename"] if atts else None,
        "pdf_base64": atts[0]["content_base64"] if atts else None,
    }
    headers = {"content-type": "application/json"}
    if settings.webhook_token:
        headers["X-Webhook-Token"] = settings.webhook_token
    await _post_json(settings.email_n8n_url, headers, payload, timeout, ok_statuses=(200, 201, 202, 204))


async def _gmail_access_token(settings, timeout: float) -> str:
    """Обновляет access token Gmail по refresh token (OAuth2)."""
    data = {
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "refresh_token": settings.gmail_refresh_token,
        "grant_type": "refresh_token",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth2.googleapis.com/token", data=data,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200 or "access_token" not in body:
                raise RuntimeError(f"Gmail OAuth {resp.status}: {str(body)[:300]}")
            return body["access_token"]


async def _send_gmail(settings, email: OutgoingEmail, timeout: float) -> None:
    """Отправка через Gmail API (HTTPS 443, OAuth2 refresh token)."""
    token = await _gmail_access_token(settings, timeout)
    msg = email.as_email_message(settings.sender_email)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    await _post_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        {"authorization": f"Bearer {token}", "content-type": "application/json"},
        {"raw": raw}, timeout, ok_statuses=(200, 201, 202),
    )


_API_SENDERS = {
    "brevo": _send_brevo, "sendgrid": _send_sendgrid, "resend": _send_resend,
    "n8n": _send_n8n, "gmail": _send_gmail,
}


# --- Публичный интерфейс ---------------------------------------------------
def _normalize_attachments(
    attachment: bytes | None, filename: str, attachments: list[tuple[str, bytes]] | None
) -> list[tuple[str, bytes]]:
    """Сводит одиночное вложение и список к единому списку (имя, байты)."""
    if attachments:
        return list(attachments)
    if attachment is not None:
        return [(filename, attachment)]
    return []


async def send_email(
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    filename: str = "document.pdf",
    attachments: list[tuple[str, bytes]] | None = None,
    meta: dict | None = None,
    timeout: float = 20.0,
) -> None:
    """Отправляет письмо выбранным транспортом (EMAIL_PROVIDER).

    Совместимо со старым вызовом (attachment + filename). Для нескольких PDF —
    передайте attachments=[(имя, байты), ...]. meta прокидывается в n8n-вебхук.
    """
    settings = get_settings()
    email = OutgoingEmail(
        to=to, subject=subject, body=body,
        attachments=_normalize_attachments(attachment, filename, attachments),
        meta=meta,
    )
    provider = (settings.email_provider or "smtp").lower()
    if provider in _API_SENDERS:
        await _API_SENDERS[provider](settings, email, timeout)
        return
    await _send_smtp(settings, email, timeout)


async def email_check(timeout: float = 15.0) -> str:
    """Проверяет доступность выбранного транспорта (без отправки письма)."""
    settings = get_settings()
    provider = (settings.email_provider or "smtp").lower()

    if provider == "n8n":
        if not settings.email_n8n_url:
            raise RuntimeError("EMAIL_N8N_URL не задан")
        return f"n8n webhook настроен ({settings.email_n8n_url}); проверьте пробной отправкой"

    if provider == "gmail":
        token = await _gmail_access_token(settings, timeout)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"Gmail API HTTP {resp.status}: {str(body)[:300]}")
                return f"gmail: OAuth OK, ящик {body.get('emailAddress', settings.sender_email)}"

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
