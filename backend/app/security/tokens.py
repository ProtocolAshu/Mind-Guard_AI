"""JWT access tokens and rotating opaque refresh tokens with reuse detection.

Refresh tokens are 256-bit random strings; only their SHA-256 is stored. Every refresh
rotates the token. Presenting an already-rotated token revokes the whole token family
(stolen-token replay defence)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.core.errors import UnauthorizedError
from app.database.models import RefreshToken, User

ALGORITHM = "HS256"


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: str
    token_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def issue_access_token(user: User, *, secret: str, issuer: str, audience: str, ttl_minutes: int, now: datetime) -> str:
    payload = {
        "sub": str(user.id), "role": user.role, "typ": "access", "iss": issuer, "aud": audience,
        "iat": int(now.timestamp()), "nbf": int(now.timestamp()) - 5,
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()), "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, *, secret: str, issuer: str, audience: str, now: datetime) -> Principal:
    try:
        # Time claims are checked below against the injected clock (testable, no hidden wall-clock reads).
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=issuer, audience=audience,
                            options={"require": ["exp", "iat", "sub", "typ", "jti"], "verify_exp": False,
                                     "verify_iat": False, "verify_nbf": False})
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("invalid access token", code="token_invalid") from exc
    if claims.get("typ") != "access":
        raise UnauthorizedError("invalid access token", code="token_invalid")
    ts = int(now.timestamp())
    try:
        exp, iat, nbf = int(claims["exp"]), int(claims["iat"]), int(claims.get("nbf", claims["iat"]))
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("invalid access token", code="token_invalid") from exc
    if exp <= ts:
        raise UnauthorizedError("access token expired", code="token_expired")
    if nbf > ts + 30 or iat > ts + 60:
        raise UnauthorizedError("access token not yet valid", code="token_invalid")
    try:
        return Principal(user_id=uuid.UUID(claims["sub"]), role=str(claims.get("role", "user")), token_id=claims["jti"])
    except (ValueError, KeyError) as exc:
        raise UnauthorizedError("invalid access token", code="token_invalid") from exc


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID, *, ttl_days: int, now: datetime,
                              family_id: uuid.UUID | None = None) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(32)
    row = RefreshToken(user_id=user_id, token_hash=_hash(raw), family_id=family_id or uuid.uuid4(),
                       expires_at=now + timedelta(days=ttl_days), created_at=now)
    session.add(row)
    await session.flush()
    return raw, row


async def rotate_refresh_token(session: AsyncSession, raw: str, *, ttl_days: int, now: datetime) -> tuple[str, RefreshToken]:
    row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw)).with_for_update())
           ).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("invalid refresh token", code="refresh_invalid")
    if row.revoked_at is not None:
        await revoke_family(session, row.family_id, now)
        raise UnauthorizedError("refresh token reuse detected; all sessions for this login were revoked", code="refresh_reuse")
    if ensure_utc(row.expires_at) <= now:
        raise UnauthorizedError("refresh token expired", code="refresh_expired")
    new_raw, new_row = await issue_refresh_token(session, row.user_id, ttl_days=ttl_days, now=now, family_id=row.family_id)
    row.revoked_at = now
    row.replaced_by = new_row.id
    return new_raw, new_row


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(update(RefreshToken).where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
                          .values(revoked_at=now))


async def revoke_token(session: AsyncSession, raw: str, now: datetime) -> None:
    row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw)))).scalar_one_or_none()
    if row is not None:
        await revoke_family(session, row.family_id, now)
