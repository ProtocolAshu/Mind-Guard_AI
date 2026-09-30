"""Dashboard analytics (section 11) computed from aggregated daily features.

Attention score (0-100) — transparent, documented heuristic, not a clinical measure:
  100 * (0.35 * (1 - min(social/limit, 1.5) / 1.5) + 0.25 * (1 - long_session_rate)
         + 0.15 * (1 - night_share) + 0.15 * min(focus_minutes / 120, 1) + 0.10 * acceptance_rate)
where `limit` is the user's daily social limit (default 120 minutes).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BehaviorFeature, Intervention, InterventionOutcome, Memory, RiskScore, UserPreference
from app.policies import catalog
from app.services.behavior import build_profile, trigger_hours
from app.services.usage import local_day_bounds, recompute_daily_features

DEFAULT_LIMIT = 120.0


def attention_score(social_minutes: float, limit_minutes: float, long_rate: float, night_share: float, focus_minutes: float,
                    acceptance_rate: float) -> int:
    limit = limit_minutes or DEFAULT_LIMIT
    score = (0.35 * (1 - min(social_minutes / limit, 1.5) / 1.5) + 0.25 * (1 - long_rate) + 0.15 * (1 - night_share)
             + 0.15 * min(focus_minutes / 120.0, 1.0) + 0.10 * acceptance_rate)
    return round(100 * max(0.0, min(1.0, score)))


async def _feature(session: AsyncSession, user_id: uuid.UUID, day: date, tz: ZoneInfo, today: date) -> BehaviorFeature | None:
    if day >= today - timedelta(days=1):
        return await recompute_daily_features(session, user_id, day, tz)
    return (await session.execute(select(BehaviorFeature).where(BehaviorFeature.user_id == user_id,
                                                                BehaviorFeature.feature_date == day))).scalar_one_or_none()


def _day_dict(day: date, f: BehaviorFeature | None, limit: float) -> dict[str, Any]:
    if f is None:
        return {"date": day.isoformat(), "social_minutes": 0.0, "screen_minutes": 0.0, "productive_minutes": 0.0,
                "focus_minutes": 0.0, "sessions": 0, "long_sessions": 0, "opens": 0, "night_minutes": 0.0,
                "interventions": 0, "accepted": 0, "overridden": 0, "hourly_social_minutes": [0.0] * 24, "top_apps": [],
                "attention_score": attention_score(0, limit, 0, 0, 0, 0)}
    social = f.total_social_seconds / 60
    outcomes = f.accepted_count + f.overridden_count
    long_rate = f.long_sessions_count / f.sessions_count if f.sessions_count else 0.0
    night_share = f.night_social_seconds / f.total_social_seconds if f.total_social_seconds else 0.0
    apps = sorted((f.app_seconds or {}).items(), key=lambda kv: -kv[1])[:5]
    return {
        "date": day.isoformat(), "social_minutes": round(social, 1), "screen_minutes": round(f.total_screen_seconds / 60, 1),
        "productive_minutes": round(f.productive_seconds / 60, 1), "focus_minutes": round(f.focus_seconds / 60, 1),
        "sessions": f.sessions_count, "long_sessions": f.long_sessions_count, "opens": f.opens_count,
        "night_minutes": round(f.night_social_seconds / 60, 1), "interventions": f.interventions_count,
        "accepted": f.accepted_count, "overridden": f.overridden_count,
        "hourly_social_minutes": [round(v / 60, 1) for v in (f.hourly_social_seconds or [0] * 24)],
        "top_apps": [{"package": p, "name": (catalog.lookup(p).name if catalog.lookup(p) else p), "minutes": round(s / 60, 1)}  # type: ignore[union-attr]
                     for p, s in apps],
        "attention_score": attention_score(social, limit, long_rate, night_share, f.focus_seconds / 60,
                                           f.accepted_count / outcomes if outcomes else 0.0),
    }


async def daily(session: AsyncSession, user_id: uuid.UUID, day: date, prefs: UserPreference, tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    today = now.astimezone(tz).date()
    limit = float(prefs.daily_social_limit_minutes or DEFAULT_LIMIT)
    out = _day_dict(day, await _feature(session, user_id, day, tz, today), limit)
    start, end = local_day_bounds(day, tz)
    rows = (await session.execute(select(Intervention.final_decision, InterventionOutcome.outcome)
                                  .outerjoin(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
                                  .where(Intervention.user_id == user_id, Intervention.created_at >= start,
                                         Intervention.created_at < end))).all()
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for decision, outcome in rows:
        by_type[decision]["total"] += 1
        by_type[decision][outcome or "pending"] += 1
    out["interventions_by_type"] = {k: dict(v) for k, v in by_type.items()}
    resolved = [o for _, o in rows if o is not None]
    out["intervention_success_rate"] = round(sum(o == "accepted" for o in resolved) / len(resolved), 3) if resolved else None
    risk = (await session.execute(select(func.avg(RiskScore.distraction_risk), func.max(RiskScore.distraction_risk),
                                         func.avg(RiskScore.doomscroll_probability), func.count())
                                  .where(RiskScore.user_id == user_id, RiskScore.created_at >= start, RiskScore.created_at < end))).one()
    out["distraction_score"] = round(float(risk[0]) * 100) if risk[3] else None
    out["peak_distraction_score"] = round(float(risk[1]) * 100) if risk[3] else None
    out["doomscroll_score"] = round(float(risk[2]) * 100) if risk[3] else None
    out["risk_assessments"] = int(risk[3])
    out["daily_limit_minutes"] = limit
    return out


async def trends(session: AsyncSession, user_id: uuid.UUID, days: int, prefs: UserPreference, tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    today = now.astimezone(tz).date()
    limit = float(prefs.daily_social_limit_minutes or DEFAULT_LIMIT)
    first = today - timedelta(days=days - 1)
    rows = {f.feature_date: f for f in (await session.execute(select(BehaviorFeature).where(
        BehaviorFeature.user_id == user_id, BehaviorFeature.feature_date >= first))).scalars()}
    rows[today] = await recompute_daily_features(session, user_id, today, tz)
    series = []
    for i in range(days):
        day = first + timedelta(days=i)
        d = _day_dict(day, rows.get(day), limit)
        series.append({k: d[k] for k in ("date", "social_minutes", "screen_minutes", "productive_minutes", "focus_minutes",
                                         "long_sessions", "interventions", "attention_score")})
    return {"days": days, "series": series}


async def weekly(session: AsyncSession, user_id: uuid.UUID, end_day: date, prefs: UserPreference, tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    today = now.astimezone(tz).date()
    limit = float(prefs.daily_social_limit_minutes or DEFAULT_LIMIT)
    days = [end_day - timedelta(days=6 - i) for i in range(7)]
    feats = {d: await _feature(session, user_id, d, tz, today) for d in days}
    previous = (await session.execute(select(BehaviorFeature).where(
        BehaviorFeature.user_id == user_id, BehaviorFeature.feature_date >= end_day - timedelta(days=13),
        BehaviorFeature.feature_date <= end_day - timedelta(days=7)))).scalars().all()
    daily_rows = [_day_dict(d, feats[d], limit) for d in days]
    total_social = sum(r["social_minutes"] for r in daily_rows)
    prev_social = sum(f.total_social_seconds for f in previous) / 60
    hourly = [0] * 24
    for f in feats.values():
        if f is not None:
            for h, v in enumerate((f.hourly_social_seconds or [])[:24]):
                hourly[h] += v
    start, _ = local_day_bounds(days[0], tz)
    _, end = local_day_bounds(end_day, tz)
    outcome_rows = (await session.execute(select(Intervention.final_decision, InterventionOutcome.outcome, InterventionOutcome.reward)
                                          .join(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
                                          .where(Intervention.user_id == user_id, Intervention.created_at >= start,
                                                 Intervention.created_at < end))).all()
    eff: dict[str, list[float]] = defaultdict(list)
    overrides = 0
    for decision, outcome, reward in outcome_rows:
        eff[decision].append(float(reward))
        overrides += outcome in ("overridden", "disabled_protection")
    sessions = sum(r["sessions"] for r in daily_rows)
    long_sessions = sum(r["long_sessions"] for r in daily_rows)
    return {
        "start": days[0].isoformat(), "end": end_day.isoformat(), "days": daily_rows,
        "totals": {"social_minutes": round(total_social, 1), "focus_minutes": round(sum(r["focus_minutes"] for r in daily_rows), 1),
                   "sessions": sessions, "long_sessions": long_sessions,
                   "interventions": sum(r["interventions"] for r in daily_rows), "overrides": overrides},
        "averages": {"social_minutes_per_day": round(total_social / 7, 1),
                     "attention_score": round(sum(r["attention_score"] for r in daily_rows) / 7, 1)},
        "change_vs_previous_week_pct": round((total_social - prev_social) / prev_social * 100, 1) if prev_social else None,
        "trigger_hours": trigger_hours(hourly),
        "intervention_effectiveness": {k: {"n": len(v), "mean_reward": round(sum(v) / len(v), 3)} for k, v in eff.items()},
        "override_rate": round(overrides / len(outcome_rows), 3) if outcome_rows else None,
    }


async def insights(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[dict[str, Any]]:
    profile = await build_profile(session, user_id, now=now, days=14)
    out: list[dict[str, Any]] = []
    if profile.trigger_hours:
        start, end = profile.trigger_hours[0], (profile.trigger_hours[-1] + 1) % 24
        out.append({"kind": "trigger_period", "source": "statistics",
                    "headline": f"You are most likely to enter long scrolling sessions between {start:02d}:00 and {end:02d}:00.",
                    "recommendation": f"Enable automatic focus protection from {start:02d}:00.",
                    "evidence": [f"{profile.avg_daily_social_minutes:.0f} social minutes/day over {profile.days_observed} days"]})
    for pattern in profile.patterns:
        out.append({"kind": "pattern", "source": "statistics", "headline": pattern, "recommendation": None, "evidence": []})
    memories = (await session.execute(select(Memory).where(Memory.user_id == user_id, Memory.memory_type.in_(["insight", "preference"]))
                                      .order_by(Memory.created_at.desc()).limit(20))).scalars()
    for m in memories:
        meta = m.meta or {}
        if m.expires_at is not None and m.expires_at <= now:
            continue
        if meta.get("kind") == "policy_suggestion" and meta.get("status") != "open":
            continue
        out.append({"kind": meta.get("kind", m.memory_type), "source": m.source, "headline": m.content,
                    "recommendation": None, "evidence": [], "memory_id": str(m.id), "actionable": meta.get("kind") == "policy_suggestion"})
    return out[:12]
