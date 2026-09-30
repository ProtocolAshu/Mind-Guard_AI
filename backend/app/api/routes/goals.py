from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_now, get_session
from app.database.models import Event, Goal, User
from app.policies.compiler import goal_relevant_categories
from app.schemas.api import GoalCreate, GoalOut, GoalUpdate
from app.schemas.common import EventType, GoalStatus
from app.security.audit import append_audit
from app.services.goals import get_goal, list_goals

router = APIRouter(prefix="/api/goals", tags=["Goals"])


def _out(goal: Goal) -> GoalOut:
    out = GoalOut.model_validate(goal)
    return out.model_copy(update={"relevant_categories": goal_relevant_categories(f"{goal.title} {goal.description}")})


async def _changed(session: AsyncSession, user: User, goal: Goal, action: str, now: datetime, details: dict[str, object]) -> None:
    session.add(Event(user_id=user.id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}", event_type=EventType.GOAL_UPDATED.value,
                      occurred_at=now, received_at=now, payload={"goal_id": str(goal.id), "action": action}))
    await append_audit(session, actor=f"user:{user.id}", action=f"goal.{action}", resource_type="goal", resource_id=goal.id,
                       details=details, user_id=user.id, now=now)


@router.post("", response_model=GoalOut, status_code=201)
async def create_goal(body: GoalCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      now: datetime = Depends(get_now)) -> GoalOut:
    goal = Goal(user_id=user.id, title=body.title.strip(), description=body.description, priority=body.priority,
                is_temporary=body.is_temporary or body.ends_at is not None, starts_at=body.starts_at, ends_at=body.ends_at,
                status=GoalStatus.ACTIVE.value, created_at=now, updated_at=now)
    session.add(goal)
    await session.flush()
    await _changed(session, user, goal, "created", now, {"title": goal.title, "temporary": goal.is_temporary})
    return _out(goal)


@router.get("", response_model=list[GoalOut])
async def get_goals(include_archived: bool = False, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> list[GoalOut]:
    return [_out(g) for g in await list_goals(session, user.id, include_archived=include_archived)]


@router.get("/{goal_id}", response_model=GoalOut)
async def read_goal(goal_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> GoalOut:
    return _out(await get_goal(session, user.id, goal_id))


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(goal_id: uuid.UUID, body: GoalUpdate, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> GoalOut:
    goal = await get_goal(session, user.id, goal_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(goal, key, value.value if hasattr(value, "value") else value)
    if goal.starts_at and goal.ends_at and goal.ends_at <= goal.starts_at:
        from app.core.errors import ValidationFailedError

        raise ValidationFailedError("ends_at must be after starts_at")
    goal.updated_at = now
    await _changed(session, user, goal, "updated", now, {"fields": sorted(changes)})
    return _out(goal)


@router.delete("/{goal_id}", status_code=204)
async def archive_goal(goal_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                       now: datetime = Depends(get_now)) -> None:
    goal = await get_goal(session, user.id, goal_id)
    goal.status = GoalStatus.ARCHIVED.value
    goal.updated_at = now
    await _changed(session, user, goal, "archived", now, {})
