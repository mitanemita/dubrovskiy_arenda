"""Тесты почтового транспорта: нормализация вложений и payload n8n."""
import base64
from types import SimpleNamespace

import pytest

from app.email import sender
from app.email.sender import OutgoingEmail, _normalize_attachments


def test_normalize_attachments_single_and_list():
    # одиночное вложение сводится к списку
    assert _normalize_attachments(b"%PDF", "a.pdf", None) == [("a.pdf", b"%PDF")]
    # явный список приоритетнее
    atts = [("a.pdf", b"1"), ("b.pdf", b"2")]
    assert _normalize_attachments(b"ignored", "x.pdf", atts) == atts
    # без вложений — пустой список
    assert _normalize_attachments(None, "x.pdf", None) == []


def test_attachments_b64_encodes_pdf():
    email = OutgoingEmail(to="t@ex.ru", subject="s", body="b", attachments=[("doc.pdf", b"%PDF-DATA")])
    atts = email.attachments_b64()
    assert len(atts) == 1
    assert atts[0]["filename"] == "doc.pdf"
    assert atts[0]["mime_type"] == "application/pdf"
    assert base64.b64decode(atts[0]["content_base64"]) == b"%PDF-DATA"


def test_as_email_message_has_attachments():
    email = OutgoingEmail(to="t@ex.ru", subject="Тема", body="Текст",
                          attachments=[("a.pdf", b"1"), ("b.pdf", b"2")])
    msg = email.as_email_message("from@ex.ru")
    assert msg["To"] == "t@ex.ru"
    assert msg["From"] == "from@ex.ru"
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["a.pdf", "b.pdf"]


@pytest.mark.asyncio
async def test_send_email_n8n_payload(monkeypatch):
    """EMAIL_PROVIDER=n8n → в вебхук уходит самодостаточный JSON с вложениями и meta."""
    captured = {}

    async def fake_post(url, headers, payload, timeout, ok_statuses=(200, 201, 202)):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload

    fake_settings = SimpleNamespace(
        email_provider="n8n",
        email_n8n_url="http://n8n:5678/webhook/send-email",
        webhook_token="secret123",
        email_from="owner@ex.ru",
        email_login="",
    )
    # sender_email — свойство в реальном Settings; тут задаём напрямую
    fake_settings.sender_email = "owner@ex.ru"

    monkeypatch.setattr(sender, "_post_json", fake_post)
    monkeypatch.setattr(sender, "get_settings", lambda: fake_settings)

    await sender.send_email(
        "tenant@ex.ru", "УПД №1", "Тело письма",
        attachment=b"%PDF-1", filename="upd.pdf",
        meta={"document_type": "upd", "kind": "electricity"},
    )

    assert captured["url"] == "http://n8n:5678/webhook/send-email"
    assert captured["headers"]["X-Webhook-Token"] == "secret123"
    p = captured["payload"]
    assert p["to"] == "tenant@ex.ru"
    assert p["subject"] == "УПД №1"
    assert p["from"] == "owner@ex.ru"
    assert p["meta"] == {"document_type": "upd", "kind": "electricity"}
    assert len(p["attachments"]) == 1
    assert p["attachments"][0]["filename"] == "upd.pdf"
    assert base64.b64decode(p["attachments"][0]["content_base64"]) == b"%PDF-1"
    # legacy-поля первого вложения — для простых воркфлоу
    assert p["filename"] == "upd.pdf"
    assert base64.b64decode(p["pdf_base64"]) == b"%PDF-1"
