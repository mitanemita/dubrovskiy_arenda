"""Справочники: создание и выборка помещений, арендаторов, договоров, счётчиков.

Вводятся оператором через бота (инлайн-мастера). Вся бизнес-валидация — здесь,
чтобы её можно было покрыть тестами независимо от Telegram-слоя.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import LeaseStatus, OrgType
from app.db.models import Landlord, Lease, Meter, Premises, Tenant

# Реквизиты арендодателя, правимые через бота: поле -> (подпись, тип валидации)
LANDLORD_FIELDS: dict[str, str] = {
    "name": "Наименование",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН/ОГРНИП",
    "address": "Адрес",
    "bank_name": "Банк",
    "bik": "БИК",
    "account": "Расчётный счёт",
    "corr_account": "Корр. счёт",
}


def _clean_inn(inn: str) -> str:
    """Проверяет ИНН: только цифры, длина 10 (юрлицо) или 12 (ИП/физлицо)."""
    digits = inn.strip()
    if not digits.isdigit() or len(digits) not in (10, 12):
        raise ValueError("ИНН должен состоять из 10 или 12 цифр.")
    return digits


# --- Реквизиты арендодателя ------------------------------------------------
async def get_landlord(session: AsyncSession, landlord_id: int) -> Landlord | None:
    return await session.get(Landlord, landlord_id)


async def update_landlord_field(session: AsyncSession, landlord_id: int, field: str, value: str) -> Landlord:
    """Обновляет одно поле реквизитов арендодателя (с валидацией по полю)."""
    if field not in LANDLORD_FIELDS:
        raise ValueError(f"Недопустимое поле: {field}")
    value = value.strip()
    if field in ("name",) and not value:
        raise ValueError("Наименование не может быть пустым.")
    if field == "inn":
        value = _clean_inn(value)
    if field == "kpp" and value and (not value.isdigit() or len(value) != 9):
        raise ValueError("КПП должен состоять из 9 цифр.")
    if field == "bik" and value and (not value.isdigit() or len(value) != 9):
        raise ValueError("БИК должен состоять из 9 цифр.")
    if field in ("account", "corr_account") and value and (not value.isdigit() or len(value) != 20):
        raise ValueError("Номер счёта должен состоять из 20 цифр.")
    landlord = await session.get(Landlord, landlord_id)
    if landlord is None:
        raise ValueError("Арендодатель не найден.")
    setattr(landlord, field, value or None)
    return landlord


# --- Помещения -------------------------------------------------------------
async def create_premises(
    session: AsyncSession,
    *,
    landlord_id: int,
    label: str,
    address: str | None = None,
    area: Decimal | None = None,
    is_occupied: bool = False,
) -> Premises:
    label = label.strip()
    if not label:
        raise ValueError("Название помещения не может быть пустым.")
    if area is not None and area <= 0:
        raise ValueError("Площадь должна быть больше нуля.")
    premises = Premises(
        landlord_id=landlord_id, label=label, address=address, area=area, is_occupied=is_occupied
    )
    session.add(premises)
    return premises


async def list_premises(session: AsyncSession, landlord_id: int) -> list[Premises]:
    result = await session.execute(
        select(Premises).where(Premises.landlord_id == landlord_id).order_by(Premises.id)
    )
    return list(result.scalars().all())


async def set_premises_status(session: AsyncSession, premises_id: int, is_occupied: bool) -> Premises | None:
    """Переключает занятость помещения (свободно/занято), без привязки к арендатору."""
    premises = await session.get(Premises, premises_id)
    if premises is not None:
        premises.is_occupied = is_occupied
    return premises


async def active_occupants(session: AsyncSession, landlord_id: int) -> dict[int, list[str]]:
    """Кто занимает помещения: {premises_id: [имена арендаторов по активным договорам]}."""
    rows = (await session.execute(
        select(Lease.premises_id, Tenant.name)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Tenant.landlord_id == landlord_id, Lease.status == LeaseStatus.active)
        .order_by(Lease.premises_id)
    )).all()
    result: dict[int, list[str]] = {}
    for pid, name in rows:
        result.setdefault(pid, []).append(name)
    return result


# --- Арендаторы ------------------------------------------------------------
async def create_tenant(
    session: AsyncSession,
    *,
    landlord_id: int,
    name: str,
    type: OrgType,
    inn: str,
    kpp: str | None = None,
    address: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> Tenant:
    name = name.strip()
    if not name:
        raise ValueError("Наименование арендатора не может быть пустым.")
    tenant = Tenant(
        landlord_id=landlord_id,
        name=name,
        type=type,
        inn=_clean_inn(inn),
        kpp=kpp,
        address=address,
        email=email,
        phone=phone,
    )
    session.add(tenant)
    return tenant


async def list_tenants(session: AsyncSession, landlord_id: int) -> list[Tenant]:
    result = await session.execute(
        select(Tenant).where(Tenant.landlord_id == landlord_id).order_by(Tenant.id)
    )
    return list(result.scalars().all())


# --- Договоры --------------------------------------------------------------
async def create_lease(
    session: AsyncSession,
    *,
    tenant_id: int,
    premises_id: int,
    contract_no: str,
    contract_date: date,
    rent_amount: Decimal,
    payment_day: int = 5,
) -> Lease:
    contract_no = contract_no.strip()
    if not contract_no:
        raise ValueError("Номер договора не может быть пустым.")
    if rent_amount <= 0:
        raise ValueError("Сумма аренды должна быть больше нуля.")
    if not 1 <= payment_day <= 31:
        raise ValueError("День оплаты должен быть в диапазоне 1..31.")
    lease = Lease(
        tenant_id=tenant_id,
        premises_id=premises_id,
        contract_no=contract_no,
        contract_date=contract_date,
        rent_amount=rent_amount,
        payment_day=payment_day,
        status=LeaseStatus.active,
    )
    session.add(lease)
    # Привязка арендатора к помещению -> помещение занято.
    premises = await session.get(Premises, premises_id)
    if premises is not None:
        premises.is_occupied = True
    return lease


async def list_leases(session: AsyncSession, landlord_id: int) -> list[Lease]:
    """Договоры арендодателя (через связь арендатора)."""
    result = await session.execute(
        select(Lease)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Tenant.landlord_id == landlord_id)
        .order_by(Lease.id)
    )
    return list(result.scalars().all())


async def get_lease(session: AsyncSession, lease_id: int) -> Lease | None:
    return await session.get(Lease, lease_id)


async def _sync_occupancy(session: AsyncSession, premises_id: int) -> None:
    """Пересчитывает занятость помещения по наличию активных договоров."""
    premises = await session.get(Premises, premises_id)
    if premises is None:
        return
    has_active = (await session.execute(
        select(Lease.id).where(Lease.premises_id == premises_id, Lease.status == LeaseStatus.active).limit(1)
    )).first() is not None
    premises.is_occupied = has_active


async def update_lease(
    session: AsyncSession,
    lease_id: int,
    *,
    rent_amount: Decimal | None = None,
    payment_day: int | None = None,
    premises_id: int | None = None,
) -> Lease | None:
    """Правит договор. При смене помещения освобождает старое и занимает новое."""
    lease = await session.get(Lease, lease_id)
    if lease is None:
        return None
    if rent_amount is not None:
        if rent_amount <= 0:
            raise ValueError("Сумма аренды должна быть больше нуля.")
        lease.rent_amount = rent_amount
    if payment_day is not None:
        if not 1 <= payment_day <= 31:
            raise ValueError("День оплаты должен быть в диапазоне 1..31.")
        lease.payment_day = payment_day
    if premises_id is not None and premises_id != lease.premises_id:
        old = lease.premises_id
        lease.premises_id = premises_id
        await session.flush()
        await _sync_occupancy(session, old)
        await _sync_occupancy(session, premises_id)
    return lease


async def delete_lease(session: AsyncSession, lease_id: int) -> bool:
    """Удаляет договор (с начислениями/платежами) и освобождает помещение при необходимости."""
    lease = await session.get(Lease, lease_id)
    if lease is None:
        return False
    premises_id = lease.premises_id
    await session.delete(lease)
    await session.flush()
    await _sync_occupancy(session, premises_id)
    return True


async def delete_tenant(session: AsyncSession, tenant_id: int) -> bool:
    """Удаляет арендатора (каскадно его договоры) и освобождает их помещения."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return False
    premises_ids = (await session.execute(
        select(Lease.premises_id).where(Lease.tenant_id == tenant_id)
    )).scalars().all()
    await session.delete(tenant)
    await session.flush()
    for pid in set(premises_ids):
        await _sync_occupancy(session, pid)
    return True


async def delete_meter(session: AsyncSession, meter_id: int) -> bool:
    meter = await session.get(Meter, meter_id)
    if meter is None:
        return False
    await session.delete(meter)
    return True


async def delete_premises(session: AsyncSession, premises_id: int) -> bool:
    """Удаляет помещение. Нельзя, если на нём есть договоры (сначала удалите их)."""
    premises = await session.get(Premises, premises_id)
    if premises is None:
        return False
    has_lease = (await session.execute(
        select(Lease.id).where(Lease.premises_id == premises_id).limit(1)
    )).first() is not None
    if has_lease:
        raise ValueError("У помещения есть договоры — сначала удалите их.")
    await session.delete(premises)
    return True


# Поля арендатора/помещения, правимые через бота: ключ -> подпись
TENANT_FIELDS: dict[str, str] = {
    "name": "Наименование",
    "inn": "ИНН",
    "kpp": "КПП",
    "address": "Адрес",
    "email": "Email",
    "phone": "Телефон",
}
PREMISES_FIELDS: dict[str, str] = {
    "label": "Название/№",
    "address": "Адрес",
    "area": "Площадь, м²",
}


async def update_tenant_field(session: AsyncSession, tenant_id: int, field: str, value: str) -> Tenant:
    if field not in TENANT_FIELDS:
        raise ValueError(f"Недопустимое поле: {field}")
    value = value.strip()
    if field == "name" and not value:
        raise ValueError("Наименование не может быть пустым.")
    if field == "inn":
        value = _clean_inn(value)
    if field == "kpp" and value and (not value.isdigit() or len(value) != 9):
        raise ValueError("КПП должен состоять из 9 цифр.")
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Арендатор не найден.")
    setattr(tenant, field, value if field in ("name", "inn") else (value or None))
    return tenant


async def update_premises_field(session: AsyncSession, premises_id: int, field: str, value: str) -> Premises:
    if field not in PREMISES_FIELDS:
        raise ValueError(f"Недопустимое поле: {field}")
    value = value.strip()
    premises = await session.get(Premises, premises_id)
    if premises is None:
        raise ValueError("Помещение не найдено.")
    if field == "label":
        if not value:
            raise ValueError("Название не может быть пустым.")
        premises.label = value
    elif field == "address":
        premises.address = value or None
    elif field == "area":
        if not value:
            premises.area = None
        else:
            try:
                area = Decimal(value.replace(",", "."))
            except Exception as exc:  # noqa: BLE001
                raise ValueError("Площадь должна быть числом.") from exc
            if area <= 0:
                raise ValueError("Площадь должна быть больше нуля.")
            premises.area = area
    return premises


# --- Счётчики --------------------------------------------------------------
async def create_meter(
    session: AsyncSession,
    *,
    premises_id: int,
    serial_no: str | None = None,
    label: str | None = None,
    coefficient: Decimal | None = None,
) -> Meter:
    if coefficient is not None and coefficient <= 0:
        raise ValueError("Коэффициент должен быть больше нуля.")
    meter = Meter(
        premises_id=premises_id,
        serial_no=serial_no,
        label=label,
        coefficient=coefficient,
    )
    session.add(meter)
    return meter


async def list_meters(session: AsyncSession, landlord_id: int) -> list[tuple[Meter, str]]:
    """Счётчики арендодателя со ссылкой на помещение (Meter, premises_label)."""
    result = await session.execute(
        select(Meter, Premises.label)
        .join(Premises, Premises.id == Meter.premises_id)
        .where(Premises.landlord_id == landlord_id)
        .order_by(Meter.id)
    )
    return [(m, label) for m, label in result.all()]
