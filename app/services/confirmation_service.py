"""Обработка решения арендодателя по платежу (подтвердить/отклонить).

Вынесено из бота отдельным сервисом, чтобы логику можно было тестировать без aiogram.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import NotifChannel
from app.db.models import Lease, Payment
from app.services import matching_service, notification_service, payment_service


async def process_payment_decision(
    session: AsyncSession,
    payment: Payment,
    *,
    approve: bool,
    user_id: int | None,
    today: date | None = None,
) -> dict:
    """Подтверждает/отклоняет платёж и уведомляет арендатора.

    При подтверждении разносит платёж по начислениям; при неполной оплате
    ставит арендатору уведомление о новом счёте на недоплату.
    """
    today = today or date.today()
    lease = await session.get(Lease, payment.lease_id)
    landlord_id = await matching_service.landlord_id_for_lease(session, lease)

    if not approve:
        await payment_service.reject_payment(session, payment, rejected_by_id=user_id)
        await notification_service.enqueue(
            session,
            landlord_id=landlord_id,
            channel=NotifChannel.email,
            type="payment_rejected",
            tenant_id=lease.tenant_id,
            subject="Оплата не подтверждена",
            body="Оплата не подтверждена арендодателем. Пожалуйста, свяжитесь с арендодателем.",
            related_payment_id=payment.id,
        )
        return {"approved": False}

    summary = await payment_service.confirm_payment(session, payment, confirmed_by_id=user_id, today=today)

    if summary["fully_paid"]:
        await notification_service.enqueue(
            session,
            landlord_id=landlord_id,
            channel=NotifChannel.email,
            type="payment_confirmed",
            tenant_id=lease.tenant_id,
            subject="Оплата подтверждена",
            body="Ваша оплата подтверждена. Благодарим!",
            related_payment_id=payment.id,
        )
    else:
        await notification_service.enqueue(
            session,
            landlord_id=landlord_id,
            channel=NotifChannel.email,
            type="payment_confirmed_partial",
            tenant_id=lease.tenant_id,
            subject="Оплата подтверждена частично",
            body=(
                f"Оплата подтверждена частично. Остаток к доплате: {summary['remaining_debt']} ₽. "
                f"Новый счёт на недоплату во вложении."
            ),
            related_payment_id=payment.id,
        )

    return {"approved": True, **summary}
