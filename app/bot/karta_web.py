"""Мини-веб-сервер внутри бота: страница карты базы по подписанной ссылке.

Запускается, только если задан PUBLIC_BASE_URL. Использует aiohttp, который уже
идёт вместе с aiogram (новых зависимостей нет). Карта собирается из БД при
каждом открытии — всегда актуальная.
"""
from __future__ import annotations

from aiohttp import web

from app.config import get_settings
from app.db.base import async_session_factory
from app.services import karta_link, karta_service
from app.utils.logger import logger

_EXPIRED = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<body style='font-family:system-ui;padding:24px'>"
    "<h3>Ссылка устарела</h3><p>Нажмите «🔄 Обновить» под картой в боте.</p></body>"
)


async def _karta(request: web.Request) -> web.Response:
    landlord_id = karta_link.verify_token(request.match_info["token"])
    if landlord_id is None:
        return web.Response(text=_EXPIRED, status=404, content_type="text/html")
    async with async_session_factory() as session:
        html, _ = await karta_service.render_html(session, landlord_id)
    return web.Response(
        body=html, content_type="text/html", charset="utf-8",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/karta/{token}", _karta)
    app.router.add_get("/health", _health)
    return app


async def start() -> web.AppRunner | None:
    """Запускает сервер, если включён. Ошибки не роняют бота (карта уйдёт файлом)."""
    if karta_link.base_url() is None:
        return None
    try:
        runner = web.AppRunner(build_app(), access_log=None)
        await runner.setup()
        port = get_settings().karta_port
        await web.TCPSite(runner, "0.0.0.0", port).start()
        logger.info("Карта базы по ссылке: порт %s, адрес %s", port, karta_link.base_url())
        return runner
    except Exception:
        logger.exception("Не удалось запустить веб-страницу карты — карта будет отправляться файлом")
        return None
