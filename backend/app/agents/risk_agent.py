"""Risk Agent: maps agent state to the shared feature definition and scores it."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RiskScore
from app.risk.features import RiskInput
from app.schemas.agents import BehaviorProfile, ContentAssessment, ContextSnapshot, RiskAssessment
from app.schemas.common import ContentCategory, GoalRelevance, PolicyEffect
from app.schemas.policy import PolicyVerdict


def risk_input_from(context: ContextSnapshot, behavior: BehaviorProfile, content: ContentAssessment | None,
                    verdict: PolicyVerdict) -> RiskInput:
    return RiskInput(
        session_minutes=context.session_minutes,
        hour=context.minute_of_day // 60,
        minute=context.minute_of_day % 60,
        weekday=context.weekday,
        focus_active=context.focus_session_active,
        in_restricted_window=verdict.in_restricted_window and verdict.effect is not PolicyEffect.ALLOW,
        content_category=content.category if content else ContentCategory.UNKNOWN,
        content_confidence=content.confidence if content else 0.0,
        goal_relevant=bool(content and content.goal_relevance is GoalRelevance.RELEVANT),
        app_category=context.app_category,
        opens_last_hour=context.opens_last_hour,
        daily_social_minutes=context.daily_social_minutes,
        daily_limit_minutes=context.daily_limit_minutes,
        trigger_hours=tuple(behavior.trigger_hours),
        avg_session_minutes_14d=behavior.avg_session_minutes,
        long_session_rate_14d=behavior.long_session_rate,
        override_rate_14d=behavior.override_rate,
        accept_rate_14d=behavior.acceptance_rate,
        minutes_since_last_intervention=context.minutes_since_last_intervention,
        scroll_events=context.scroll_events,
        history_days=behavior.days_observed,
    )


async def persist_risk(session: AsyncSession, *, user_id: uuid.UUID, run_id: uuid.UUID | None,
                       session_id: uuid.UUID | None, risk: RiskAssessment, now: datetime) -> RiskScore:
    row = RiskScore(user_id=user_id, session_id=session_id, run_id=run_id, distraction_risk=risk.distraction_risk,
                    doomscroll_probability=risk.doomscroll_probability, goal_conflict=risk.goal_conflict,
                    intervention_urgency=risk.intervention_urgency, continuation_probability=risk.continuation_probability,
                    predicted_productivity_loss_minutes=risk.predicted_productivity_loss_minutes, confidence=risk.confidence,
                    method=risk.method.value, factors={"factors": [f.model_dump(mode="json") for f in risk.factors]},
                    model_version=(risk.model_version or None) and risk.model_version[:255], created_at=now)
    session.add(row)
    await session.flush()
    return row
