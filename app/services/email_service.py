"""Диспетчер email-очереди: генерирует нужный PDF и отправляет письма арендаторам."""
from __future__ import annotations

from datetime import date
from typing import Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import NotifChannel
from app.db.models import Charge, Notification, Payment, Tenant
from app.services import document_service, notification_dispatch
from app.utils.logger import logger

# Типы писем, к которым прикрепляется квитанция (PDF)
_NEED_RECEIPT = {"invoice_new", "reminder", "overdue", "payment_confirmed_partial"}


class EmailSender(Protocol):
    async def __call__(
        self, to: str, subject: str, body: str, attachment: bytes | None = None, filename: str = "document.pdf"
    ) -> None: ...


# Функция генерации квитанции (внедряется для тестируемости)
ReceiptPdfFn = Callable[[AsyncSession, int, date], Awaitable[tuple[bytes, str]]]


async def _resolve_lease_period(session: AsyncSession, notif: Notification) -> tuple[int, date] | None:
    """Определяет договор и период для квитанции по привязке уведомления."""
    if notif.related_charge_id:
        charge = await session.get(Charge, notif.related_charge_id)
        if charge:
            return charge.lease_id, charge.period
    if notif.related_payment_id:
        payment = await session.get(Payment, notif.related_payment_id)
        if payment:
            return payment.lease_id, payment.period or date.today()
    return None


async def dispatch_email(
    session: AsyncSession,
    send: EmailSender,
    receipt_pdf: ReceiptPdfFn | None = None,
) -> dict:
    """Отправляет очередные email-уведомления. К счетам/напоминаниям прикрепляет квитанцию."""
    receipt_pdf = receipt_pdf or document_service.receipt_pdf
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    for notif in await notification_dispatch.get_pending(session, NotifChannel.email):
        tenant = await session.get(Tenant, notif.tenant_id) if notif.tenant_id else None
        if tenant is None or not tenant.email:
            notification_dispatch.mark_failed(notif)
            stats["skipped"] += 1
            logger.warning("Email-уведомление %s без адреса арендатора", notif.id)
            continue

        attachment: bytes | None = None
        filename = "document.pdf"
        if notif.type in _NEED_RECEIPT:
            resolved = await _resolve_lease_period(session, notif)
            if resolved is not None:
                lease_id, period = resolved
                try:
                    attachment, filename = await receipt_pdf(session, lease_id, period)
                except Exception:
                    logger.exception("Не удалось сформировать квитанцию для уведомления %s", notif.id)

        try:
            await send(
                to=tenant.email,
                subject=notif.subject or "Уведомление",
                body=notif.body or "",
                attachment=attachment,
                filename=filename,
            )
            notification_dispatch.mark_sent(notif)
            stats["sent"] += 1
        except Exception:
            notification_dispatch.mark_failed(notif)
            stats["failed"] += 1
            logger.exception("Ошибка отправки email-уведомления %s", notif.id)

    return stats
