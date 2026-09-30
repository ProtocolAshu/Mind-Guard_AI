from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_now, get_session
from app.core.errors import UnauthorizedError, ValidationFailedError
from app.database.models import Consent, RefreshToken, User
from app.schemas.api import (
    CONSENT_DESCRIPTIONS,
    ConsentOut,
    ConsentUpdate,
    DeviceUpdate,
    PasswordChange,
    PreferencesOut,
    PreferencesUpdate,
    SettingsOut,
    UserOut,
    UserUpdate,
)
from app.schemas.common import ConsentScope
from app.security.audit import append_audit
from app.security.passwords import hash_password, password_problems, verify_password
from app.services.users import get_preferences, set_consent

router = APIRouter(tags=["Users", "Settings"])


@router.get("/api/users/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/api/users/me", response_model=UserOut)
async def update_me(body: UserUpdate, user: User = Depends(get_current_user), now: datetime = Depends(get_now)) -> UserOut:
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
        user.updated_at = now
    return UserOut.model_validate(user)


@router.post("/api/users/me/password", status_code=204)
async def change_password(body: PasswordChange, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise UnauthorizedError("current password is incorrect", code="invalid_credentials")
    problems = password_problems(body.new_password)
    if problems:
        raise ValidationFailedError("password is too weak", code="weak_password", details={"requirements": problems})
    user.password_hash = hash_password(body.new_password)
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                          .values(revoked_at=now))
    await append_audit(session, actor=f"user:{user.id}", action="user.password_changed", resource_type="user",
                       resource_id=user.id, details={}, user_id=user.id, now=now)


async def _consents(session: AsyncSession, user: User) -> list[ConsentOut]:
    rows = {c.scope: c for c in (await session.execute(select(Consent).where(Consent.user_id == user.id))).scalars()}
    return [ConsentOut(scope=scope, granted=bool(rows[scope.value].granted) if scope.value in rows else False,
                       description=CONSENT_DESCRIPTIONS[scope],
                       updated_at=rows[scope.value].updated_at if scope.value in rows else None) for scope in ConsentScope]


@router.get("/api/settings", response_model=SettingsOut)
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> SettingsOut:
    prefs = await get_preferences(session, user.id)
    return SettingsOut(preferences=PreferencesOut.model_validate(prefs), consents=await _consents(session, user))


@router.patch("/api/settings", response_model=PreferencesOut)
@router.put("/api/settings", response_model=PreferencesOut,
            description="Same partial update as PATCH, for HTTP clients that cannot send PATCH (e.g. some HttpURLConnection builds).")
async def update_settings(body: PreferencesUpdate, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> PreferencesOut:
    prefs = await get_preferences(session, user.id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(prefs, key, value.value if hasattr(value, "value") else value)
    prefs.updated_at = now
    if changes:
        await append_audit(session, actor=f"user:{user.id}", action="settings.updated", resource_type="preferences",
                           resource_id=user.id, details={"fields": sorted(changes)}, user_id=user.id, now=now)
    await session.flush()
    return PreferencesOut.model_validate(prefs)


@router.get("/api/settings/consents", response_model=list[ConsentOut])
async def list_consents(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[ConsentOut]:
    return await _consents(session, user)


@router.put("/api/settings/consents/{scope}", response_model=ConsentOut)
async def put_consent(scope: ConsentScope, body: ConsentUpdate, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> ConsentOut:
    row = await set_consent(session, user.id, scope, body.granted, actor=f"user:{user.id}", now=now)
    return ConsentOut(scope=scope, granted=row.granted, description=CONSENT_DESCRIPTIONS[scope], updated_at=row.updated_at)


@router.put("/api/settings/device", response_model=PreferencesOut)
async def put_device(body: DeviceUpdate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                     now: datetime = Depends(get_now)) -> PreferencesOut:
    prefs = await get_preferences(session, user.id)
    prefs.device_capabilities = body.capabilities.model_dump()
    prefs.updated_at = now
    await session.flush()
    return PreferencesOut.model_validate(prefs)
