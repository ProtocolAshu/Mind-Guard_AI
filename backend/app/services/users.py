"""Preferences and consent (sections 14, 25)."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Consent, Event, UserPreference
from app.schemas.agents import DeviceCapabilities
from app.schemas.common import ConsentScope, EventType
from app.security.audit import append_audit


async def get_preferences(session: AsyncSession, user_id: uuid.UUID) -> UserPreference:
    prefs = await session.get(UserPreference, user_id)
    if prefs is None:
        prefs = UserPreference(user_id=user_id)
        session.add(prefs)
        await session.flush()
    return prefs


def user_timezone(prefs: UserPreference) -> ZoneInfo:
    try:
        return ZoneInfo(prefs.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def device_capabilities(prefs: UserPreference) -> DeviceCapabilities:
    raw = prefs.device_capabilities or {}
    return DeviceCapabilities.model_validate({k: raw.get(k) for k in DeviceCapabilities.model_fields})


async def granted_scopes(session: AsyncSession, user_id: uuid.UUID) -> frozenset[ConsentScope]:
    rows = (await session.execute(select(Consent.scope).where(Consent.user_id == user_id, Consent.granted.is_(True)))).scalars()
    return frozenset(ConsentScope(s) for s in rows)


async def set_consent(
    session: AsyncSession, user_id: uuid.UUID, scope: ConsentScope, granted: bool, *, actor: str, now: datetime
) -> Consent:
    row = (await session.execute(select(Consent).where(Consent.user_id == user_id, Consent.scope == scope.value))).scalar_one_or_none()
    previous = row.granted if row else None
    if row is None:
        row = Consent(user_id=user_id, scope=scope.value, granted=granted, updated_at=now)
        session.add(row)
    else:
        row.granted = granted
        row.updated_at = now
    if previous != granted:
        session.add(
            Event(user_id=user_id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}", event_type=EventType.CONSENT_CHANGED.value,
                  occurred_at=now, received_at=now, payload={"scope": scope.value, "granted": granted})
        )
        await append_audit(session, actor=actor, action="consent.changed", resource_type="consent", resource_id=scope.value,
                           details={"granted": granted, "previous": previous}, user_id=user_id, now=now)
    await session.flush()
    return row
