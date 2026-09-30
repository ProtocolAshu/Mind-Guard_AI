"""Interventions and outcomes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bandit.rewards import compute_reward
from app.core.errors import ConflictError, NotFoundError
from app.database.models import Intervention, InterventionOutcome
from app.schemas.agents import RecentIntervention
from app.schemas.common import InterventionStatus, InterventionType, OutcomeType

OUTCOME_STATUS = {
    OutcomeType.ACCEPTED: InterventionStatus.ACCEPTED,
    OutcomeType.STOPPED_SESSION: InterventionStatus.ACCEPTED,
    OutcomeType.RETURNED_TO_TASK: InterventionStatus.ACCEPTED,
    OutcomeType.OVERRIDDEN: InterventionStatus.OVERRIDDEN,
    OutcomeType.DISABLED_PROTECTION: InterventionStatus.OVERRIDDEN,
    OutcomeType.IGNORED: InterventionStatus.IGNORED,
    OutcomeType.CONTINUED_SESSION: InterventionStatus.IGNORED,
}


async def recent_interventions(session: AsyncSession, user_id: uuid.UUID, *, now: datetime, hours: int = 24) -> list[RecentIntervention]:
    rows = (
        await session.execute(
            select(Intervention)
            .where(Intervention.user_id == user_id, Intervention.created_at >= now - timedelta(hours=hours),
                   Intervention.final_decision != InterventionType.ALLOW.value)
            .order_by(Intervention.created_at.desc())
            .limit(200)
        )
    ).scalars()
    return [
        RecentIntervention(id=i.id, decision=InterventionType(i.final_decision), app_package=i.app_package,
                           status=InterventionStatus(i.status), created_at=i.created_at, expires_at=i.expires_at)
        for i in rows
    ]


async def get_intervention(session: AsyncSession, user_id: uuid.UUID, intervention_id: uuid.UUID) -> Intervention:
    row = (await session.execute(select(Intervention).where(Intervention.id == intervention_id,
                                                            Intervention.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("intervention not found")  # same response for other users' ids (no enumeration)
    return row


async def record_outcome(
    session: AsyncSession, user_id: uuid.UUID, intervention_id: uuid.UUID, outcome: OutcomeType, *,
    satisfaction: int | None, now: datetime,
) -> tuple[Intervention, InterventionOutcome]:
    intervention = await get_intervention(session, user_id, intervention_id)
    existing = (await session.execute(select(InterventionOutcome.id).where(
        InterventionOutcome.intervention_id == intervention.id))).first()
    if existing:
        raise ConflictError("outcome already recorded for this intervention", code="outcome_exists")
    reward = compute_reward(outcome, satisfaction)
    row = InterventionOutcome(intervention_id=intervention.id, user_id=user_id, outcome=outcome.value, reward=reward,
                              satisfaction=satisfaction, created_at=now)
    session.add(row)
    intervention.status = OUTCOME_STATUS[outcome].value
    intervention.resolved_at = now
    await session.flush()
    return intervention, row
