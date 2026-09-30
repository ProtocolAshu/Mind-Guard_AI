"""Data export and deletion (sections 14, 25). Audit entries survive deletion with the
user link severed (ON DELETE SET NULL) and contain no raw personal content."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import require
from app.database import models as m
from app.security.audit import append_audit

EXPORT_LIMIT = 20_000
HISTORY_TABLES: list[type[Any]] = [
    m.ToolCall, m.LLMCall, m.InterventionOutcome, m.Intervention, m.RiskScore, m.ContentSignal, m.Event, m.UsageSession,
    m.BehaviorFeature, m.Embedding, m.Memory, m.BanditState,
]
CONFIG_TABLES: list[type[Any]] = [m.RefreshToken, m.Consent, m.PolicyOverride, m.PolicyVersion, m.Policy, m.Goal, m.UserPreference]
EXPORT_TABLES: list[type[Any]] = [m.UserPreference, m.Consent, m.Goal, m.Policy, m.PolicyVersion, m.PolicyOverride, m.UsageSession,
                                  m.Event, m.ContentSignal, m.RiskScore, m.Intervention, m.InterventionOutcome, m.BehaviorFeature,
                                  m.Memory, m.BanditState, m.AgentRun, m.ToolCall, m.LLMCall]


def _value(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _row(obj: Any, exclude: set[str]) -> dict[str, Any]:
    return {c.key: _value(getattr(obj, c.key)) for c in obj.__mapper__.column_attrs if c.key not in exclude}


async def export_user_data(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    user = require(await session.get(m.User, user_id), "user missing during export")
    out: dict[str, Any] = {"format": "mindguard-export/v1", "user": _row(user, {"password_hash"}), "tables": {}}
    for model in EXPORT_TABLES:
        rows = (await session.execute(select(model).where(model.user_id == user_id).limit(EXPORT_LIMIT + 1))).scalars().all()
        exclude = {"embedding"} if model is m.Embedding else set()
        out["tables"][model.__tablename__] = {"rows": [_row(r, exclude) for r in rows[:EXPORT_LIMIT]],
                                              "truncated": len(rows) > EXPORT_LIMIT}
    run_ids = [r["id"] for r in out["tables"]["agent_runs"]["rows"]]
    steps = (await session.execute(select(m.AgentStep).where(m.AgentStep.run_id.in_([uuid.UUID(i) for i in run_ids[:2000]])))
             ).scalars().all() if run_ids else []
    out["tables"]["agent_steps"] = {"rows": [_row(s, set()) for s in steps], "truncated": len(run_ids) > 2000}
    return out


async def delete_user_data(session: AsyncSession, user_id: uuid.UUID, scope: Literal["history", "all"], *, actor: str,
                           now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    runs = [r for r in (await session.execute(select(m.AgentRun.id).where(m.AgentRun.user_id == user_id))).scalars()]
    for model in HISTORY_TABLES:
        result = await session.execute(delete(model).where(model.user_id == user_id))
        counts[model.__tablename__] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if runs:
        counts["agent_steps"] = int((await session.execute(delete(m.AgentStep).where(m.AgentStep.run_id.in_(runs)))).rowcount or 0)  # type: ignore[attr-defined]
        await session.execute(delete(m.AgentRun).where(m.AgentRun.id.in_(runs)))
    counts["agent_runs"] = len(runs)
    if scope == "all":
        for model in CONFIG_TABLES:
            result = await session.execute(delete(model).where(model.user_id == user_id))
            counts[model.__tablename__] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await append_audit(session, actor=actor, action=f"user.data_deleted.{scope}", resource_type="user", resource_id=user_id,
                       details={"counts": counts}, user_id=None if scope == "all" else user_id, now=now)
    if scope == "all":
        await session.execute(delete(m.User).where(m.User.id == user_id))
    await session.flush()
    return counts
