"""Подписанные ссылки на карту базы: <PUBLIC_BASE_URL>/karta/<token>.

Токен = landlord_id + срок действия + HMAC-подпись (ключ выводится из BOT_TOKEN).
Без БД: страница по ссылке каждый раз собирается заново из актуальных данных.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import get_settings


def _key() -> bytes | None:
    secret = get_settings().bot_token
    if not secret:
        return None
    return hashlib.sha256(b"karta-link:" + secret.encode()).digest()


def _sig(key: bytes, payload: str) -> str:
    mac = hmac.new(key, payload.encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def base_url() -> str | None:
    base = get_settings().public_base_url.strip().rstrip("/")
    return base if base.startswith(("http://", "https://")) else None


def make_token(landlord_id: int, *, days: int | None = None, now: float | None = None) -> str | None:
    key = _key()
    if key is None:
        return None
    days = days if days is not None else get_settings().karta_link_days
    exp = int((now if now is not None else time.time()) + days * 86400)
    payload = f"{landlord_id}.{exp}"
    return f"{payload}.{_sig(key, payload)}"


def verify_token(token: str, *, now: float | None = None) -> int | None:
    """landlord_id, если подпись верна и срок не истёк; иначе None."""
    key = _key()
    parts = token.split(".")
    if key is None or len(parts) != 3 or not all(p.isdigit() for p in parts[:2]):
        return None
    payload = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(parts[2], _sig(key, payload)):
        return None
    if int(parts[1]) < (now if now is not None else time.time()):
        return None
    return int(parts[0])


def map_url(landlord_id: int) -> str | None:
    """Полная ссылка на карту или None (PUBLIC_BASE_URL не задан / нет токена бота)."""
    base = base_url()
    token = make_token(landlord_id) if base else None
    return f"{base}/karta/{token}" if token else None
