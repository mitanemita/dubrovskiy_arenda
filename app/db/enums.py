"""Перечисления (enum), используемые в моделях и миграциях.

Значения enum хранятся в БД как строки. Имена типов PostgreSQL заданы
явно в моделях/миграциях, чтобы совпадали при автогенерации.
"""
from __future__ import annotations

import enum


class OrgType(str, enum.Enum):
    """Организационная форма (арендодатель/арендатор)."""

    ip = "ip"        # индивидуальный предприниматель
    ooo = "ooo"      # ООО / юрлицо
    fiz = "fiz"      # физическое лицо


class TaxMode(str, enum.Enum):
    """Налоговый режим арендодателя."""

    ausn = "ausn"    # автоматизированная УСН (без НДС)
    usn = "usn"      # УСН (без НДС)
    osno = "osno"    # ОСНО (с НДС)


class UserRole(str, enum.Enum):
    """Роль оператора бота."""

    owner = "owner"  # владелец (полный доступ)
    admin = "admin"  # доверенный оператор


class LeaseStatus(str, enum.Enum):
    active = "active"
    ended = "ended"


class PenaltyBase(str, enum.Enum):
    """База начисления пени."""

    full = "full"          # на всю сумму (аренда + коммуналка)
    rent_only = "rent_only"  # только на аренду


class ChargeType(str, enum.Enum):
    rent = "rent"
    electricity = "electricity"
    penalty = "penalty"
    other = "other"


class ChargeStatus(str, enum.Enum):
    draft = "draft"
    issued = "issued"
    sent = "sent"
    paid = "paid"
    partial = "partial"
    overdue = "overdue"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"
    partial = "partial"


class DataSource(str, enum.Enum):
    """Источник данных (платёж, показания)."""

    n8n = "n8n"
    email = "email"
    manual = "manual"


class ExpenseCategory(str, enum.Enum):
    server = "server"
    electricity = "electricity"
    salary = "salary"
    travel = "travel"        # командировочные
    repair = "repair"        # текущий ремонт
    docs = "docs"            # приведение документации
    taxes = "taxes"
    other = "other"


class ExpenseMode(str, enum.Enum):
    auto = "auto"
    manual = "manual"


class DocType(str, enum.Enum):
    upd = "upd"          # УПД 5.03 (передаточный документ)
    invoice = "invoice"  # счёт на оплату
    receipt = "receipt"  # квитанция


class DocFormat(str, enum.Enum):
    pdf = "pdf"
    xml = "xml"


class DocSentStatus(str, enum.Enum):
    draft = "draft"
    generated = "generated"
    sent = "sent"
    failed = "failed"


class NotifChannel(str, enum.Enum):
    email = "email"
    telegram = "telegram"


class NotifStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    failed = "failed"
