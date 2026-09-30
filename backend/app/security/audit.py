"""Tamper-evident audit log (hash chain).

Each entry stores `entry_hash = sha256(canonical(prev_hash, seq, actor, action, resource, details, created_at))`.
`user_id` is deliberately *not* part of the hash: account deletion severs the link
(ON DELETE SET NULL) without breaking verification, and details never carry raw content.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.database.base import utcnow
from app.database.models import AuditLog

GENESIS_HASH = "0" * 64
_AUDIT_LOCK_KEY = 0x4D474155  # "MGAU": serialises appends across API workers on PostgreSQL


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, uuid.UUID | datetime):
        return str(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_entry_hash(
    *,
    prev_hash: str,
    seq: int,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any],
    created_at: datetime,
) -> str:
    payload = canonical_json(
        {
            "prev": prev_hash,
            "seq": seq,
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details,
            "created_at": ensure_utc(created_at).isoformat(timespec="microseconds"),
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def append_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: object | None = None,
    details: dict[str, Any] | None = None,
    user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AuditLog:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_LOCK_KEY})
    last = (await session.execute(select(AuditLog.seq, AuditLog.entry_hash).order_by(AuditLog.seq.desc()).limit(1))).first()
    prev_hash = last.entry_hash if last else GENESIS_HASH
    seq = (last.seq + 1) if last else 1
    created_at = ensure_utc(now or utcnow())
    normalized = _normalize(details or {})
    rid = str(resource_id) if resource_id is not None else None
    entry = AuditLog(
        seq=seq,
        user_id=user_id,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=rid,
        details=normalized,
        prev_hash=prev_hash,
        entry_hash=compute_entry_hash(
            prev_hash=prev_hash,
            seq=seq,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=rid,
            details=normalized,
            created_at=created_at,
        ),
        created_at=created_at,
    )
    session.add(entry)
    await session.flush()
    return entry


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    checked: int
    first_invalid_seq: int | None = None
    reason: str | None = None


async def verify_audit_chain(session: AsyncSession, batch_size: int = 1000) -> ChainVerification:
    prev_hash = GENESIS_HASH
    expected_seq = 1
    checked = 0
    while True:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.seq >= expected_seq).order_by(AuditLog.seq).limit(batch_size)
            )
        ).scalars().all()
        if not rows:
            return ChainVerification(valid=True, checked=checked)
        for row in rows:
            if row.seq != expected_seq:
                return ChainVerification(False, checked, expected_seq, "sequence gap (entry deleted)")
            if row.prev_hash != prev_hash:
                return ChainVerification(False, checked, row.seq, "prev_hash does not link to previous entry")
            recomputed = compute_entry_hash(
                prev_hash=row.prev_hash,
                seq=row.seq,
                actor=row.actor,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                details=row.details,
                created_at=row.created_at,
            )
            if recomputed != row.entry_hash:
                return ChainVerification(False, checked, row.seq, "entry contents modified")
            prev_hash = row.entry_hash
            expected_seq += 1
            checked += 1
