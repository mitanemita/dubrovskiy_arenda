"""Сопоставление входящих данных (платёж/показания) с договором/помещением/счётчиком."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import LeaseStatus
from app.db.models import Landlord, Lease, Meter, Premises, Tenant


async def get_default_landlord_id(session: AsyncSession) -> int | None:
    """ID единственного арендодателя (система однопользовательская; для алертов)."""
    result = await session.execute(select(Landlord.id).order_by(Landlord.id).limit(1))
    return result.scalar_one_or_none()


async def landlord_id_for_lease(session: AsyncSession, lease: Lease) -> int:
    result = await session.execute(select(Tenant.landlord_id).where(Tenant.id == lease.tenant_id))
    return result.scalar_one()


async def _single_active_lease(session: AsyncSession, condition) -> Lease | None:
    """Возвращает договор, только если условию удовлетворяет ровно один активный договор."""
    result = await session.execute(
        select(Lease).where(condition, Lease.status == LeaseStatus.active)
    )
    leases = result.scalars().all()
    return leases[0] if len(leases) == 1 else None


async def resolve_lease(
    session: AsyncSession,
    *,
    lease_id: int | None = None,
    contract_no: str | None = None,
    premises_id: int | None = None,
    premises_label: str | None = None,
    tenant_inn: str | None = None,
) -> Lease | None:
    """Ищет договор по (в порядке приоритета): id, номеру договора, помещению, ИНН арендатора.

    Возвращает договор только при однозначном совпадении, иначе None.
    """
    if lease_id is not None:
        return await session.get(Lease, lease_id)

    if contract_no:
        lease = await _single_active_lease(session, Lease.contract_no == contract_no)
        if lease:
            return lease

    if premises_id is not None:
        lease = await _single_active_lease(session, Lease.premises_id == premises_id)
        if lease:
            return lease

    if premises_label:
        prem = await session.execute(select(Premises.id).where(Premises.label == premises_label))
        prem_ids = prem.scalars().all()
        if len(prem_ids) == 1:
            lease = await _single_active_lease(session, Lease.premises_id == prem_ids[0])
            if lease:
                return lease

    if tenant_inn:
        tenants = await session.execute(select(Tenant.id).where(Tenant.inn == tenant_inn))
        tenant_ids = tenants.scalars().all()
        if len(tenant_ids) == 1:
            lease = await _single_active_lease(session, Lease.tenant_id == tenant_ids[0])
            if lease:
                return lease

    return None


async def resolve_meter(
    session: AsyncSession,
    *,
    meter_id: int | None = None,
    meter_serial: str | None = None,
    premises_id: int | None = None,
    premises_label: str | None = None,
) -> Meter | None:
    """Ищет счётчик по id, серийному номеру или помещению (если счётчик единственный)."""
    if meter_id is not None:
        return await session.get(Meter, meter_id)

    if meter_serial:
        result = await session.execute(select(Meter).where(Meter.serial_no == meter_serial))
        meters = result.scalars().all()
        if len(meters) == 1:
            return meters[0]

    target_premises_id = premises_id
    if target_premises_id is None and premises_label:
        prem = await session.execute(select(Premises.id).where(Premises.label == premises_label))
        prem_ids = prem.scalars().all()
        if len(prem_ids) == 1:
            target_premises_id = prem_ids[0]

    if target_premises_id is not None:
        result = await session.execute(select(Meter).where(Meter.premises_id == target_premises_id))
        meters = result.scalars().all()
        if len(meters) == 1:
            return meters[0]

    return None
