"""Страница карты базы по подписанной ссылке (открывается из Telegram)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import karta_link, karta_service

router = APIRouter(tags=["karta"])


@router.get("/karta/{token}", response_class=HTMLResponse, include_in_schema=False)
async def karta_page(token: str, session: AsyncSession = Depends(get_db)) -> HTMLResponse:
    landlord_id = karta_link.verify_token(token)
    if landlord_id is None:
        raise HTTPException(status_code=404, detail="Ссылка недействительна или устарела — нажмите «Обновить» в боте")
    html, _ = await karta_service.render_html(session, landlord_id)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
