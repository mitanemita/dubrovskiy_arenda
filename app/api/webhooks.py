"""Вебхуки для n8n: приём распознанных платежей и показаний счётчиков.

n8n ТОЛЬКО распознаёт и отдаёт JSON. Вся бизнес-логика и запись в БД — здесь.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, verify_webhook_token
from app.api.schemas import IncomingPaymentIn, MeterReadingIn, WebhookResult
from app.db.enums import DataSource, NotifChannel
from app.services import (
    matching_service,
    notification_service,
    payment_service,
    reading_service,
)

router = APIRouter(prefix="/webhook", tags=["n8n"], dependencies=[Depends(verify_webhook_token)])


@router.post("/incoming-payment", response_model=WebhookResult)
async def incoming_payment(data: IncomingPaymentIn, session: AsyncSession = Depends(get_db)) -> WebhookResult:
    """Чек/квитанция об оплате от арендатора → payment(pending) + уведомления.

    При невозможности сопоставить с договором — алерт арендодателю в бот (ТЗ п.10),
    платёж не создаётся.
    """
    lease = await matching_service.resolve_lease(
        session,
        lease_id=data.lease_id,
        contract_no=data.contract_no,
        premises_id=data.premises_id,
        premises_label=data.premises_label,
        tenant_inn=data.tenant_inn,
    )

    if lease is None:
        landlord_id = await matching_service.get_default_landlord_id(session)
        if landlord_id is not None:
            await notification_service.enqueue(
                session,
                landlord_id=landlord_id,
                channel=NotifChannel.telegram,
                type="payment_unmatched",
                subject="Платёж не сопоставлен",
                body=(
                    f"Получен платёж на сумму {data.amount} ₽, но не удалось определить договор. "
                    f"Данные: помещение={data.premises_label or data.premises_id}, "
                    f"ИНН={data.tenant_inn}, договор={data.contract_no}. Требуется ручная обработка."
                ),
            )
        return WebhookResult(ok=True, matched=False, message="Договор не определён, арендодатель уведомлён")

    landlord_id = await matching_service.landlord_id_for_lease(session, lease)

    payment = await payment_service.register_payment(
        session,
        lease.id,
        data.amount,
        period=data.period,
        payment_date=data.payment_date,
        proof_file=data.proof_file,
        source=DataSource.n8n,
    )
    await session.flush()  # получить payment.id

    # Арендатору: «квитанция получена, ожидайте подтверждения»
    await notification_service.enqueue(
        session,
        landlord_id=landlord_id,
        channel=NotifChannel.email,
        type="payment_received",
        tenant_id=lease.tenant_id,
        subject="Квитанция получена",
        body="Ваша квитанция об оплате получена. Ожидайте подтверждения от арендодателя.",
        related_payment_id=payment.id,
    )
    # Арендодателю в бот: запрос подтверждения (кнопки добавит бот)
    await notification_service.enqueue(
        session,
        landlord_id=landlord_id,
        channel=NotifChannel.telegram,
        type="payment_confirm_request",
        subject="Новый платёж — подтвердите",
        body=f"Платёж {data.amount} ₽ по договору №{lease.contract_no}. Подтвердить получение?",
        related_payment_id=payment.id,
    )

    return WebhookResult(
        ok=True, matched=True, payment_id=payment.id,
        message="Платёж принят (ожидает подтверждения)",
    )


@router.post("/meter-reading", response_model=WebhookResult)
async def meter_reading(data: MeterReadingIn, session: AsyncSession = Depends(get_db)) -> WebhookResult:
    """Показания счётчика от электрика (через n8n) → запись/обновление показаний."""
    meter = await matching_service.resolve_meter(
        session,
        meter_id=data.meter_id,
        meter_serial=data.meter_serial,
        premises_id=data.premises_id,
        premises_label=data.premises_label,
    )

    if meter is None:
        landlord_id = await matching_service.get_default_landlord_id(session)
        if landlord_id is not None:
            await notification_service.enqueue(
                session,
                landlord_id=landlord_id,
                channel=NotifChannel.telegram,
                type="reading_unmatched",
                subject="Показания не сопоставлены",
                body=(
                    f"Получены показания {data.curr_value} за {data.period}, "
                    f"но счётчик не определён (serial={data.meter_serial}, "
                    f"помещение={data.premises_label or data.premises_id}). Внесите вручную через бота."
                ),
            )
        return WebhookResult(ok=True, matched=False, message="Счётчик не определён, арендодатель уведомлён")

    try:
        reading = await reading_service.upsert_reading(
            session,
            meter,
            period=data.period,
            curr_value=data.curr_value,
            prev_value=data.prev_value,
            reading_date=data.reading_date,
            source=DataSource.n8n,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await session.flush()
    return WebhookResult(
        ok=True, matched=True, reading_id=reading.id, consumption=reading.consumption,
        message="Показания записаны",
    )
