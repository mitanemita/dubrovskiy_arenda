"""ORM-модели (SQLAlchemy 2.x) целевой схемы системы аренды.

Единый источник правды. В финансовые таблицы (charges/payments/expenses)
пишет только Python — n8n лишь отдаёт распознанный JSON в вебхуки.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import enums
from app.db.base import Base


def _enum(py_enum: type, name: str) -> SAEnum:
    """Строковый enum PostgreSQL с явным именем типа и значениями из Python-enum."""
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class TimestampMixin:
    """Метки времени создания/обновления записи."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------- #
#  Арендодатель и операторы бота
# --------------------------------------------------------------------------- #
class Landlord(TimestampMixin, Base):
    """Арендодатель (продавец/исполнитель в документах)."""

    __tablename__ = "landlords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[enums.OrgType] = mapped_column(_enum(enums.OrgType, "landlord_type"), nullable=False)
    inn: Mapped[str] = mapped_column(String(12), nullable=False)
    kpp: Mapped[str | None] = mapped_column(String(9))
    ogrn: Mapped[str | None] = mapped_column(String(15))
    address: Mapped[str | None] = mapped_column(Text)
    # Банковские реквизиты — отдельными полями (вместо хрупкого парсинга строки)
    bank_name: Mapped[str | None] = mapped_column(Text)
    bik: Mapped[str | None] = mapped_column(String(9))
    account: Mapped[str | None] = mapped_column(String(20))   # расчётный счёт (р/с)
    corr_account: Mapped[str | None] = mapped_column(String(20))  # корр. счёт (к/с)
    tax_mode: Mapped[enums.TaxMode] = mapped_column(
        _enum(enums.TaxMode, "tax_mode"), nullable=False, default=enums.TaxMode.ausn
    )
    signature_path: Mapped[str | None] = mapped_column(Text)  # картинка подписи для PDF

    users: Mapped[list[User]] = relationship(back_populates="landlord", cascade="all, delete-orphan")
    premises: Mapped[list[Premises]] = relationship(back_populates="landlord", cascade="all, delete-orphan")
    tenants: Mapped[list[Tenant]] = relationship(back_populates="landlord", cascade="all, delete-orphan")
    settings: Mapped[list[Setting]] = relationship(back_populates="landlord", cascade="all, delete-orphan")


class User(TimestampMixin, Base):
    """Оператор бота (Telegram-пользователь), привязан к арендодателю."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tg_id", name="uq_users_tg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[enums.UserRole] = mapped_column(
        _enum(enums.UserRole, "user_role"), nullable=False, default=enums.UserRole.owner
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    landlord: Mapped[Landlord] = relationship(back_populates="users")


# --------------------------------------------------------------------------- #
#  Помещения, арендаторы, договоры
# --------------------------------------------------------------------------- #
class Premises(TimestampMixin, Base):
    """Помещение (объект аренды)."""

    __tablename__ = "premises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)   # название/№
    address: Mapped[str | None] = mapped_column(Text)          # товарный адрес
    area: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # м²

    landlord: Mapped[Landlord] = relationship(back_populates="premises")
    meters: Mapped[list[Meter]] = relationship(back_populates="premises", cascade="all, delete-orphan")
    leases: Mapped[list[Lease]] = relationship(back_populates="premises")


class Tenant(TimestampMixin, Base):
    """Арендатор (покупатель/заказчик в документах)."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[enums.OrgType] = mapped_column(_enum(enums.OrgType, "tenant_type"), nullable=False)
    inn: Mapped[str] = mapped_column(String(12), nullable=False)
    kpp: Mapped[str | None] = mapped_column(String(9))
    address: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))

    landlord: Mapped[Landlord] = relationship(back_populates="tenants")
    leases: Mapped[list[Lease]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Lease(TimestampMixin, Base):
    """Договор аренды: связывает арендатора и помещение, хранит условия оплаты."""

    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    premises_id: Mapped[int] = mapped_column(ForeignKey("premises.id", ondelete="RESTRICT"), nullable=False)
    contract_no: Mapped[str] = mapped_column(Text, nullable=False)
    contract_date: Mapped[date] = mapped_column(Date, nullable=False)
    rent_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_day: Mapped[int] = mapped_column(Integer, nullable=False, default=5)  # день оплаты 1..31
    penalty_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.5"))  # %/день
    penalty_base: Mapped[enums.PenaltyBase] = mapped_column(
        _enum(enums.PenaltyBase, "penalty_base"), nullable=False, default=enums.PenaltyBase.full
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[enums.LeaseStatus] = mapped_column(
        _enum(enums.LeaseStatus, "lease_status"), nullable=False, default=enums.LeaseStatus.active
    )

    tenant: Mapped[Tenant] = relationship(back_populates="leases")
    premises: Mapped[Premises] = relationship(back_populates="leases")
    charges: Mapped[list[Charge]] = relationship(back_populates="lease", cascade="all, delete-orphan")
    payments: Mapped[list[Payment]] = relationship(back_populates="lease", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
#  Счётчики и показания
# --------------------------------------------------------------------------- #
class Meter(TimestampMixin, Base):
    """Счётчик электроэнергии, привязан к помещению."""

    __tablename__ = "meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    premises_id: Mapped[int] = mapped_column(ForeignKey("premises.id", ondelete="CASCADE"), nullable=False)
    serial_no: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(Text)
    coefficient: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))  # напр. 0.93 (УСН)

    premises: Mapped[Premises] = relationship(back_populates="meters")
    readings: Mapped[list[MeterReading]] = relationship(back_populates="meter", cascade="all, delete-orphan")


class MeterReading(TimestampMixin, Base):
    """Показания счётчика за период (от электрика по почте / вручную)."""

    __tablename__ = "meter_readings"
    __table_args__ = (UniqueConstraint("meter_id", "period", name="uq_reading_meter_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meters.id", ondelete="CASCADE"), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)  # месяц (1-е число)
    prev_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    curr_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    consumption: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # curr - prev
    source: Mapped[enums.DataSource] = mapped_column(
        _enum(enums.DataSource, "reading_source"), nullable=False, default=enums.DataSource.manual
    )
    reading_date: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    meter: Mapped[Meter] = relationship(back_populates="readings")


# --------------------------------------------------------------------------- #
#  Начисления и платежи
# --------------------------------------------------------------------------- #
class Charge(TimestampMixin, Base):
    """Начисление арендатору (аренда / электричество / пеня / прочее)."""

    __tablename__ = "charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[enums.ChargeType] = mapped_column(_enum(enums.ChargeType, "charge_type"), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)  # месяц начисления
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[enums.ChargeStatus] = mapped_column(
        _enum(enums.ChargeStatus, "charge_status"), nullable=False, default=enums.ChargeStatus.draft
    )

    lease: Mapped[Lease] = relationship(back_populates="charges")
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="charge", cascade="all, delete-orphan"
    )


class Payment(TimestampMixin, Base):
    """Поступление от арендатора. Может закрывать несколько начислений (allocations)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    proof_file: Mapped[str | None] = mapped_column(Text)  # путь к чеку/скрину
    source: Mapped[enums.DataSource] = mapped_column(
        _enum(enums.DataSource, "payment_source"), nullable=False, default=enums.DataSource.manual
    )
    status: Mapped[enums.PaymentStatus] = mapped_column(
        _enum(enums.PaymentStatus, "payment_status"), nullable=False, default=enums.PaymentStatus.pending
    )
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lease: Mapped[Lease] = relationship(back_populates="payments")
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentAllocation(Base):
    """Разнос суммы платежа по конкретным начислениям (many-to-many с суммой)."""

    __tablename__ = "payment_allocations"
    __table_args__ = (UniqueConstraint("payment_id", "charge_id", name="uq_alloc_payment_charge"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    charge_id: Mapped[int] = mapped_column(ForeignKey("charges.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="allocations")
    charge: Mapped[Charge] = relationship(back_populates="allocations")


# --------------------------------------------------------------------------- #
#  Расходы, настройки, документы, уведомления, аудит
# --------------------------------------------------------------------------- #
class Expense(TimestampMixin, Base):
    """Расход арендодателя (авто/ручной)."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[enums.ExpenseCategory] = mapped_column(
        _enum(enums.ExpenseCategory, "expense_category"), nullable=False
    )
    mode: Mapped[enums.ExpenseMode] = mapped_column(
        _enum(enums.ExpenseMode, "expense_mode"), nullable=False, default=enums.ExpenseMode.manual
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Setting(TimestampMixin, Base):
    """Настройка «ключ-значение» на арендодателя (правится через бота)."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("landlord_id", "key", name="uq_settings_landlord_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    landlord: Mapped[Landlord] = relationship(back_populates="settings")


class Document(TimestampMixin, Base):
    """Сформированный документ (УПД / счёт / квитанция) и статус отправки."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id", ondelete="SET NULL"))
    charge_id: Mapped[int | None] = mapped_column(ForeignKey("charges.id", ondelete="SET NULL"))
    doc_type: Mapped[enums.DocType] = mapped_column(_enum(enums.DocType, "doc_type"), nullable=False)
    doc_format: Mapped[enums.DocFormat] = mapped_column(
        _enum(enums.DocFormat, "doc_format"), nullable=False, default=enums.DocFormat.pdf
    )
    upd_version: Mapped[str | None] = mapped_column(String(8))   # напр. "5.03"
    upd_status: Mapped[int | None] = mapped_column(Integer)      # 1 или 2
    number: Mapped[str | None] = mapped_column(Text)
    period: Mapped[date | None] = mapped_column(Date)
    file_path: Mapped[str | None] = mapped_column(Text)
    xml_path: Mapped[str | None] = mapped_column(Text)
    email_to: Mapped[str | None] = mapped_column(String(255))
    sent_status: Mapped[enums.DocSentStatus] = mapped_column(
        _enum(enums.DocSentStatus, "doc_sent_status"), nullable=False, default=enums.DocSentStatus.draft
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(TimestampMixin, Base):
    """Журнал уведомлений (почта/TG) — в т.ч. для защиты от повторной отправки."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    channel: Mapped[enums.NotifChannel] = mapped_column(_enum(enums.NotifChannel, "notif_channel"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # reminder/received/overdue/...
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    related_charge_id: Mapped[int | None] = mapped_column(ForeignKey("charges.id", ondelete="SET NULL"))
    related_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    status: Mapped[enums.NotifStatus] = mapped_column(
        _enum(enums.NotifStatus, "notif_status"), nullable=False, default=enums.NotifStatus.queued
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(TimestampMixin, Base):
    """Задача менеджера задач: приоритет + дата напоминания."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[enums.TaskPriority] = mapped_column(
        _enum(enums.TaskPriority, "task_priority"), nullable=False, default=enums.TaskPriority.medium
    )
    due_date: Mapped[date | None] = mapped_column(Date)  # дата напоминания
    status: Mapped[enums.TaskStatus] = mapped_column(
        _enum(enums.TaskStatus, "task_status"), nullable=False, default=enums.TaskStatus.open
    )
    remind_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Adjustment(Base):
    """Аудит ручных корректировок доходов/расходов за любой период."""

    __tablename__ = "adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landlord_id: Mapped[int] = mapped_column(ForeignKey("landlords.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # charge/payment/expense/...
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
