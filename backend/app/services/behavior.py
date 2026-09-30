"""Behavior Agent core: a user-level profile from aggregated daily features (section 4.3).
Only aggregate usage and outcomes are used; no sensitive attributes are inferred."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BehaviorFeature, Intervention, InterventionOutcome
from app.schemas.agents import BehaviorProfile
from app.schemas.common import InterventionType, OutcomeType

POSITIVE = {OutcomeType.ACCEPTED.value, OutcomeType.STOPPED_SESSION.value, OutcomeType.RETURNED_TO_TASK.value}
NEGATIVE = {OutcomeType.OVERRIDDEN.value, OutcomeType.DISABLED_PROTECTION.value}


def trigger_hours(hourly: list[int], min_minutes: float = 10.0, top: int = 3) -> list[int]:
    total = sum(hourly)
    if total <= 0:
        return []
    uniform = total / 24
    ranked = sorted(range(24), key=lambda h: -hourly[h])
    return sorted(h for h in ranked[:top] if hourly[h] >= 1.5 * uniform and hourly[h] / 60 >= min_minutes)


async def build_profile(session: AsyncSession, user_id: uuid.UUID, *, now: datetime, days: int = 14) -> BehaviorProfile:
    since = (now - timedelta(days=days)).date()
    feats = list((await session.execute(select(BehaviorFeature).where(BehaviorFeature.user_id == user_id,
                                                                      BehaviorFeature.feature_date >= since))).scalars())
    rows = (
        await session.execute(
            select(Intervention.final_decision, InterventionOutcome.outcome, InterventionOutcome.reward)
            .join(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
            .where(Intervention.user_id == user_id, Intervention.created_at >= now - timedelta(days=days))
        )
    ).all()
    counts: dict[str, int] = defaultdict(int)
    rewards: dict[str, list[float]] = defaultdict(list)
    positive = negative = 0
    for decision, outcome, reward in rows:
        counts[decision] += 1
        rewards[decision].append(float(reward))
        positive += outcome in POSITIVE
        negative += outcome in NEGATIVE
    total_outcomes = len(rows)
    hourly = [0] * 24
    for f in feats:
        for h, v in enumerate((f.hourly_social_seconds or [])[:24]):
            hourly[h] += int(v)
    social = sum(f.total_social_seconds for f in feats)
    sessions = sum(f.sessions_count for f in feats)
    long_sessions = sum(f.long_sessions_count for f in feats)
    night = sum(f.night_social_seconds for f in feats)
    n_days = len(feats)
    avg_daily = social / 60 / n_days if n_days else 0.0
    long_rate = long_sessions / sessions if sessions else 0.0
    night_share = night / social if social else 0.0
    override_rate = negative / total_outcomes if total_outcomes else 0.0
    triggers = trigger_hours(hourly)
    effectiveness = {k: round(sum(v) / len(v), 3) for k, v in rewards.items() if v}
    patterns: list[str] = []
    if n_days and long_rate >= 0.25:
        patterns.append(f"{long_rate:.0%} of social sessions last 30+ minutes")
    if triggers:
        patterns.append("Most social-media time falls between " + ", ".join(f"{h:02d}:00" for h in triggers))
    if night_share >= 0.3:
        patterns.append(f"{night_share:.0%} of social-media time is late at night")
    hard = [d for d in (InterventionType.TEMPORARY_BLOCK.value, InterventionType.LIMITED_ACCESS.value) if counts.get(d, 0) >= 2]
    for d in hard:
        if effectiveness.get(d, 0.0) < 0:
            patterns.append(f"{d.replace('_', ' ').lower()} is often overridden ({effectiveness[d]:+.2f} mean reward)")
    for d, mean in effectiveness.items():
        if counts[d] >= 2 and mean >= 0.5 and d not in hard:
            patterns.append(f"{d.replace('_', ' ').lower()} tends to work ({mean:+.2f} mean reward)")
    risk = min(1.0, 0.35 * long_rate + 0.25 * min(avg_daily / 180.0, 1.0) + 0.2 * night_share + 0.2 * override_rate)
    return BehaviorProfile(
        days_observed=n_days,
        avg_daily_social_minutes=round(avg_daily, 1),
        avg_session_minutes=round(social / 60 / sessions, 1) if sessions else 0.0,
        long_session_rate=round(long_rate, 3),
        avg_opens_per_day=round(sum(f.opens_count for f in feats) / n_days, 1) if n_days else 0.0,
        trigger_hours=triggers,
        night_usage_share=round(night_share, 3),
        intervention_counts=dict(counts),
        acceptance_rate=round(positive / total_outcomes, 3) if total_outcomes else 0.0,
        override_rate=round(override_rate, 3),
        effectiveness_by_intervention=effectiveness,
        user_level_risk=round(risk, 3),
        patterns=patterns[:6],
    )
