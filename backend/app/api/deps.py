"""FastAPI dependencies: container, DB session (one transaction per request), auth, rate limits."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container
from app.core.errors import ForbiddenError, RateLimitedError, UnauthorizedError
from app.database.models import User
from app.security.tokens import Principal, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_session(container: Container = Depends(get_container)) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def get_now(container: Container = Depends(get_container)) -> datetime:
    return container.clock.now()


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("authentication required", code="auth_required")
    s = container.settings
    return decode_access_token(credentials.credentials, secret=container.jwt_secret, issuer=s.jwt_issuer,
                               audience=s.jwt_audience, now=container.clock.now())


async def get_current_user(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)) -> User:
    user = await session.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("account is not active", code="account_inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise ForbiddenError("administrator role required")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit_attr: str, *, per_user: bool = True) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request, container: Container = Depends(get_container),
                         credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
        limit = int(getattr(container.settings, limit_attr))
        identity = client_ip(request)
        if per_user and credentials is not None:
            identity = "u:" + hashlib.sha256(credentials.credentials.encode()).hexdigest()[:24]
        allowed, retry_after = await container.rate_limiter.hit(f"{bucket}:{identity}", limit, 60)
        if not allowed:
            raise RateLimitedError("too many requests", retry_after=retry_after)

    return dependency
