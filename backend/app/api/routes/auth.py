from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_container, get_current_user, get_now, get_session, rate_limit
from app.container import Container
from app.core.errors import ConflictError, ForbiddenError, RateLimitedError, UnauthorizedError, ValidationFailedError
from app.database.models import User, UserPreference
from app.schemas.api import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut
from app.security.audit import append_audit
from app.security.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.security.tokens import issue_access_token, issue_refresh_token, revoke_token, rotate_refresh_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
AUTH_LIMIT = Depends(rate_limit("auth", "rate_limit_auth_per_minute", per_user=False))


async def _tokens(container: Container, session: AsyncSession, user: User, now: datetime) -> TokenResponse:
    s = container.settings
    access = issue_access_token(user, secret=container.jwt_secret, issuer=s.jwt_issuer, audience=s.jwt_audience,
                                ttl_minutes=s.access_token_ttl_minutes, now=now)
    refresh, _ = await issue_refresh_token(session, user.id, ttl_days=s.refresh_token_ttl_days, now=now)
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=s.access_token_ttl_minutes * 60, refresh_expires_in=s.refresh_token_ttl_days * 86400,
                         user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenResponse, status_code=201, dependencies=[AUTH_LIMIT])
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session),
                   container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> TokenResponse:
    if not container.settings.allow_registration:
        raise ForbiddenError("registration is disabled")
    problems = password_problems(body.password)
    if problems:
        raise ValidationFailedError("password is too weak", code="weak_password", details={"requirements": problems})
    if (await session.execute(select(User.id).where(User.email == body.email))).first():
        raise ConflictError("an account with this email already exists", code="email_taken")
    admins = {e.strip().lower() for e in container.settings.admin_emails.split(",") if e.strip()}
    user = User(email=body.email, password_hash=hash_password(body.password), display_name=body.display_name.strip(),
                role="admin" if body.email in admins else "user", created_at=now, updated_at=now)
    session.add(user)
    await session.flush()
    session.add(UserPreference(user_id=user.id, timezone=body.timezone, retention_days=container.settings.default_retention_days))
    await append_audit(session, actor=f"user:{user.id}", action="user.registered", resource_type="user", resource_id=user.id,
                       details={"role": user.role}, user_id=user.id, now=now)
    return await _tokens(container, session, user, now)


@router.post("/login", response_model=TokenResponse, dependencies=[AUTH_LIMIT])
async def login(body: LoginRequest, request: Request, session: AsyncSession = Depends(get_session),
                container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> TokenResponse:
    email = body.email.strip().lower()
    allowed, retry = await container.rate_limiter.hit(f"login-email:{email}", 20, 900)
    if not allowed:
        raise RateLimitedError("too many login attempts for this account", retry_after=retry)
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or user is None or not user.is_active:
        raise UnauthorizedError("invalid email or password", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await append_audit(session, actor=f"user:{user.id}", action="user.login", resource_type="user", resource_id=user.id,
                       details={"ip_class": "private" if client_ip(request).startswith(("10.", "192.168.", "127.")) else "public"},
                       user_id=user.id, now=now)
    return await _tokens(container, session, user, now)


@router.post("/refresh", response_model=TokenResponse, dependencies=[AUTH_LIMIT])
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session),
                  container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> TokenResponse:
    try:
        raw, row = await rotate_refresh_token(session, body.refresh_token, ttl_days=container.settings.refresh_token_ttl_days, now=now)
    except UnauthorizedError as exc:
        if exc.code == "refresh_reuse":
            await session.commit()  # persist the family revocation before responding with 401
        raise
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("account is not active", code="account_inactive")
    s = container.settings
    access = issue_access_token(user, secret=container.jwt_secret, issuer=s.jwt_issuer, audience=s.jwt_audience,
                                ttl_minutes=s.access_token_ttl_minutes, now=now)
    return TokenResponse(access_token=access, refresh_token=raw, expires_in=s.access_token_ttl_minutes * 60, refresh_expires_in=s.refresh_token_ttl_days * 86400,
                         user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> None:
    await revoke_token(session, body.refresh_token, now)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
