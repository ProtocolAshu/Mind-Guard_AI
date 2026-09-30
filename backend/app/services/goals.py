"""Goals: permanent and temporary (section 4.2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.core.errors import NotFoundError
from app.database.models import Goal
from app.policies.compiler import goal_relevant_categories
from app.schemas.agents import GoalSnapshot
from app.schemas.common import GoalStatus


def goal_is_active(goal: Goal, now: datetime) -> bool:
    if goal.status != GoalStatus.ACTIVE.value:
        return False
    if goal.starts_at and ensure_utc(goal.starts_at) > now:
        return False
    return not (goal.ends_at and ensure_utc(goal.ends_at) <= now)


def snapshot(goal: Goal) -> GoalSnapshot:
    return GoalSnapshot(
        id=goal.id,
        title=goal.title,
        description=goal.description,
        priority=goal.priority,
        is_temporary=goal.is_temporary,
        relevant_categories=goal_relevant_categories(f"{goal.title} {goal.description}"),
    )


async def list_goals(session: AsyncSession, user_id: uuid.UUID, include_archived: bool = False) -> list[Goal]:
    stmt = select(Goal).where(Goal.user_id == user_id).order_by(Goal.priority, Goal.created_at)
    if not include_archived:
        stmt = stmt.where(Goal.status != GoalStatus.ARCHIVED.value)
    return list((await session.execute(stmt)).scalars())


async def active_goals(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[GoalSnapshot]:
    return [snapshot(g) for g in await list_goals(session, user_id) if goal_is_active(g, now)]


async def get_goal(session: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> Goal:
    goal = (await session.execute(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))).scalar_one_or_none()
    if goal is None:
        raise NotFoundError("goal not found")
    return goal
