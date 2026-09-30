"""Policies, versions and user overrides (sections 12, 25)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.core.errors import NotFoundError, ValidationFailedError
from app.database.models import Event, Goal, Policy, PolicyOverride, PolicyVersion
from app.policies.engine import validate_rules
from app.schemas.agents import OverrideSnapshot
from app.schemas.common import CompiledBy, EventType, OverrideKind, PolicyStatus
from app.schemas.policy import CompiledConstitution, PolicyRule
from app.security.audit import append_audit
from app.services.goals import goal_is_active

log = logging.getLogger(__name__)


async def active_rules(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[PolicyRule]:
    rows = (
        await session.execute(
            select(Policy, PolicyVersion, Goal)
            .join(PolicyVersion, (PolicyVersion.policy_id == Policy.id) & (PolicyVersion.version == Policy.current_version))
            .outerjoin(Goal, Goal.id == Policy.goal_id)
            .where(Policy.user_id == user_id, Policy.status == PolicyStatus.ACTIVE.value)
            .order_by(Policy.updated_at.desc())
        )
    ).all()
    rules: list[PolicyRule] = []
    seen: set[str] = set()
    for _policy, version, goal in rows:
        if goal is not None and not goal_is_active(goal, now):
            continue  # temporary goal (e.g. exam week) not active: its policy is dormant
        for raw in version.rules:
            try:
                rule = PolicyRule.model_validate(raw)
            except ValidationError:
                log.warning("skipping stored rule that no longer validates", extra={"policy_version": str(version.id)})
                continue
            if rule.rule_id not in seen:
                seen.add(rule.rule_id)
                rules.append(rule)
    return rules


def _validated(rules: list[PolicyRule]) -> dict[str, object]:
    report = validate_rules(rules)
    if not report.valid:
        raise ValidationFailedError(
            "policy has errors", code="policy_invalid", details={"issues": [i.model_dump() for i in report.issues]}
        )
    return report.model_dump()


async def create_policy(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    name: str,
    rules: list[PolicyRule],
    source_text: str,
    compiled_by: CompiledBy,
    goal_id: uuid.UUID | None,
    actor: str,
    now: datetime,
) -> tuple[Policy, PolicyVersion]:
    validation = _validated(rules)
    policy = Policy(user_id=user_id, goal_id=goal_id, name=name[:120], current_version=1, created_at=now, updated_at=now)
    session.add(policy)
    await session.flush()
    version = PolicyVersion(policy_id=policy.id, user_id=user_id, version=1, rules=[r.model_dump(mode="json") for r in rules],
                            source_text=source_text[:10_000], compiled_by=compiled_by.value, validation=validation, created_at=now)
    session.add(version)
    await _policy_event(session, user_id, policy.id, 1, now)
    await append_audit(session, actor=actor, action="policy.created", resource_type="policy", resource_id=policy.id,
                       details={"version": 1, "rules": len(rules), "compiled_by": compiled_by.value}, user_id=user_id, now=now)
    await session.flush()
    return policy, version


async def save_constitution(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    constitution: CompiledConstitution,
    source_text: str,
    compiled_by: CompiledBy,
    actor: str,
    now: datetime,
    name: str = "Personal AI Constitution",
) -> tuple[Policy, PolicyVersion, Goal | None]:
    """Persist a reviewed constitution: its goal (if stated) and a policy linked to that goal."""
    goal = None
    if constitution.goal is not None:
        goal = Goal(user_id=user_id, title=constitution.goal.title[:120], description=constitution.goal.description[:2000],
                    status="active", priority=1, created_at=now, updated_at=now)
        session.add(goal)
        await session.flush()
        await append_audit(session, actor=actor, action="goal.created", resource_type="goal", resource_id=goal.id,
                           details={"title": goal.title, "source": "constitution"}, user_id=user_id, now=now)
    policy, version = await create_policy(session, user_id, name=name, rules=list(constitution.rules), source_text=source_text,
                                          compiled_by=compiled_by, goal_id=goal.id if goal else None, actor=actor, now=now)
    return policy, version, goal


async def get_policy(session: AsyncSession, user_id: uuid.UUID, policy_id: uuid.UUID) -> Policy:
    policy = (await session.execute(select(Policy).where(Policy.id == policy_id, Policy.user_id == user_id))).scalar_one_or_none()
    if policy is None:
        raise NotFoundError("policy not found")
    return policy


async def current_version(session: AsyncSession, policy: Policy) -> PolicyVersion:
    return (
        await session.execute(
            select(PolicyVersion).where(PolicyVersion.policy_id == policy.id, PolicyVersion.version == policy.current_version)
        )
    ).scalar_one()


async def update_policy(
    session: AsyncSession,
    user_id: uuid.UUID,
    policy_id: uuid.UUID,
    *,
    rules: list[PolicyRule],
    source_text: str,
    compiled_by: CompiledBy,
    actor: str,
    now: datetime,
    name: str | None = None,
) -> tuple[Policy, PolicyVersion]:
    policy = await get_policy(session, user_id, policy_id)
    validation = _validated(rules)
    policy.current_version += 1
    policy.updated_at = now
    if name:
        policy.name = name[:120]
    version = PolicyVersion(policy_id=policy.id, user_id=user_id, version=policy.current_version,
                            rules=[r.model_dump(mode="json") for r in rules], source_text=source_text[:10_000],
                            compiled_by=compiled_by.value, validation=validation, created_at=now)
    session.add(version)
    await _policy_event(session, user_id, policy.id, policy.current_version, now)
    await append_audit(session, actor=actor, action="policy.updated", resource_type="policy", resource_id=policy.id,
                       details={"version": policy.current_version, "rules": len(rules), "compiled_by": compiled_by.value},
                       user_id=user_id, now=now)
    await session.flush()
    return policy, version


async def set_policy_status(session: AsyncSession, user_id: uuid.UUID, policy_id: uuid.UUID, status: PolicyStatus, *,
                            actor: str, now: datetime) -> Policy:
    policy = await get_policy(session, user_id, policy_id)
    previous = policy.status
    policy.status = status.value
    policy.updated_at = now
    await append_audit(session, actor=actor, action="policy.status", resource_type="policy", resource_id=policy.id,
                       details={"from": previous, "to": status.value}, user_id=user_id, now=now)
    await session.flush()
    return policy


async def _policy_event(session: AsyncSession, user_id: uuid.UUID, policy_id: uuid.UUID, version: int, now: datetime) -> None:
    session.add(Event(user_id=user_id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}", event_type=EventType.POLICY_UPDATED.value,
                      occurred_at=now, received_at=now, payload={"policy_id": str(policy_id), "version": version}))


# --------------------------------------------------------------------------- overrides

def override_expiry(kind: OverrideKind, now: datetime, tz: ZoneInfo) -> datetime | None:
    if kind is OverrideKind.ALLOW_ONCE:
        return now + timedelta(minutes=20)
    if kind is OverrideKind.DISABLE_30M:
        return now + timedelta(minutes=30)
    if kind is OverrideKind.DISABLE_UNTIL_TOMORROW:
        local = now.astimezone(tz)
        target = local.replace(hour=6, minute=0, second=0, microsecond=0)
        if target <= local + timedelta(hours=1):
            target += timedelta(days=1)
        return target.astimezone(now.tzinfo)
    if kind is OverrideKind.EMERGENCY:
        return now + timedelta(hours=24)
    return None  # PAUSE lasts until the user resumes


async def active_overrides(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[OverrideSnapshot]:
    rows = (
        await session.execute(
            select(PolicyOverride).where(
                PolicyOverride.user_id == user_id,
                PolicyOverride.revoked_at.is_(None),
                PolicyOverride.consumed_at.is_(None),
            )
        )
    ).scalars()
    return [
        OverrideSnapshot(id=o.id, kind=OverrideKind(o.kind), app_package=o.app_package, expires_at=o.expires_at)
        for o in rows
        if o.expires_at is None or ensure_utc(o.expires_at) > now
    ]


async def create_override(
    session: AsyncSession, user_id: uuid.UUID, kind: OverrideKind, *, app_package: str | None, reason: str | None,
    tz: ZoneInfo, actor: str, now: datetime,
) -> PolicyOverride:
    row = PolicyOverride(user_id=user_id, kind=kind.value, app_package=app_package, reason=(reason or None) and reason[:280],
                         starts_at=now, expires_at=override_expiry(kind, now, tz), created_at=now)
    session.add(row)
    await session.flush()
    event_type = {OverrideKind.EMERGENCY: EventType.EMERGENCY_OVERRIDE}.get(kind, EventType.GUARDIAN_PAUSED)
    if kind is not OverrideKind.ALLOW_ONCE:
        session.add(Event(user_id=user_id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}", event_type=event_type.value,
                          occurred_at=now, received_at=now, payload={"override_id": str(row.id), "kind": kind.value}))
    await append_audit(session, actor=actor, action="override.created", resource_type="override", resource_id=row.id,
                       details={"kind": kind.value, "app_package": app_package,
                                "expires_at": row.expires_at.isoformat() if row.expires_at else None},
                       user_id=user_id, now=now)
    return row


async def revoke_override(session: AsyncSession, user_id: uuid.UUID, override_id: uuid.UUID, *, actor: str,
                          now: datetime) -> PolicyOverride:
    row = (await session.execute(select(PolicyOverride).where(PolicyOverride.id == override_id,
                                                              PolicyOverride.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("override not found")
    if row.revoked_at is None:
        row.revoked_at = now
        if row.kind != OverrideKind.ALLOW_ONCE.value:
            session.add(Event(user_id=user_id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}",
                              event_type=EventType.GUARDIAN_RESUMED.value, occurred_at=now, received_at=now,
                              payload={"override_id": str(row.id)}))
        await append_audit(session, actor=actor, action="override.revoked", resource_type="override", resource_id=row.id,
                           details={"kind": row.kind}, user_id=user_id, now=now)
    return row


async def consume_override(session: AsyncSession, user_id: uuid.UUID, override_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(PolicyOverride)
        .where(PolicyOverride.id == override_id, PolicyOverride.user_id == user_id, PolicyOverride.consumed_at.is_(None))
        .values(consumed_at=now)
    )
