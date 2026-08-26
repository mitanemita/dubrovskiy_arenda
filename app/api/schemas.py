"""Pydantic-схемы вебхуков n8n (валидация входа и формат ответа)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncomingPaymentIn(BaseModel):
    """Распознанный n8n чек/квитанция об оплате от арендатора.

    Для сопоставления с договором достаточно любого из идентификаторов
    (lease_id / contract_no / premises_id / premises_label / tenant_inn).
    """

    model_config = ConfigDict(extra="ignore")

    amount: Decimal = Field(gt=0, description="Сумма платежа, ₽")
    payment_date: date | None = Field(default=None, description="Дата платежа")
    period: date | None = Field(default=None, description="Период (месяц), за который платёж")

    lease_id: int | None = None
    contract_no: str | None = None
    premises_id: int | None = None
    premises_label: str | None = None
    tenant_inn: str | None = None

    proof_file: str | None = Field(default=None, description="Ссылка/путь к файлу чека")


class MeterReadingIn(BaseModel):
    """Показания счётчика (от электрика по почте, распознаны n8n)."""

    model_config = ConfigDict(extra="ignore")

    period: date = Field(description="Период (месяц) показаний")
    curr_value: Decimal = Field(ge=0, description="Текущие показания")
    prev_value: Decimal | None = Field(default=None, ge=0, description="Предыдущие показания (если нет — из истории)")
    reading_date: date | None = None

    meter_id: int | None = None
    meter_serial: str | None = None
    premises_id: int | None = None
    premises_label: str | None = None

    @model_validator(mode="after")
    def _has_meter_identifier(self) -> "MeterReadingIn":
        if not any([self.meter_id, self.meter_serial, self.premises_id, self.premises_label]):
            raise ValueError("Нужен идентификатор счётчика: meter_id/meter_serial/premises_id/premises_label")
        return self


class WebhookResult(BaseModel):
    """Унифицированный ответ вебхука."""

    ok: bool
    matched: bool
    message: str
    payment_id: int | None = None
    reading_id: int | None = None
    consumption: Decimal | None = None
