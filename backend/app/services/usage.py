"""Event ingestion, sessions, daily behaviour features and the context snapshot
(sections 4.3, 4.5, 19, 41). High-frequency signals are aggregated on the device
into APP_OPENED / SESSION_EXTENDED heartbeats, so nothing here calls a model."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.database.models import BehaviorFeature, Event, Intervention, InterventionOutcome, RiskScore, UsageSession
from app.policies import catalog
from app.risk.features import LONG_SESSION_MINUTES, is_night_hour
from app.schemas.agents import ContextSnapshot, GoalSnapshot
from app.schemas.common import (
    CLIENT_EVENT_TYPES,
    HARD_INTERVENTIONS,
    AppCategory,
    EventType,
    InterventionType,
    OutcomeType,
)
from app.schemas.policy import PACKAGE_PATTERN

SOCIAL = frozenset({AppCategory.SOCIAL_MEDIA.value, AppCategory.VIDEO.value})
PRODUCTIVE_APPS = frozenset({AppCategory.EDUCATION.value, AppCategory.PRODUCTIVITY.value})
SESSION_GAP = timedelta(minutes=5)
STALE_SESSION = timedelta(minutes=30)
SOFT = frozenset({InterventionType.SOFT_WARNING.value, InterventionType.MINDFUL_PROMPT.value,
                  InterventionType.REQUEST_CONFIRMATION.value})


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenPayload(_Payload):
    app_category: AppCategory | None = None


class ProgressPayload(_Payload):
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    scroll_events: int = Field(default=0, ge=0, le=200_000)


class FocusStartPayload(_Payload):
    planned_minutes: int = Field(default=25, ge=5, le=240)
    goal_id: uuid.UUID | None = None


class FocusEndPayload(_Payload):
    completed: bool = False


class SummaryPayload(_Payload):
    date: date
    app_seconds: dict[str, int] = Field(default_factory=dict, max_length=300)

    @field_validator("app_seconds")
    @classmethod
    def _packages(cls, v: dict[str, int]) -> dict[str, int]:
        import re

        for pkg, secs in v.items():
            if not re.fullmatch(PACKAGE_PATTERN, pkg) or not 0 <= secs <= 86_400:
                raise ValueError("invalid app usage entry")
        return v


PAYLOADS: dict[EventType, type[_Payload]] = {
    EventType.APP_OPENED: OpenPayload,
    EventType.SESSION_STARTED: OpenPayload,
    EventType.SESSION_EXTENDED: ProgressPayload,
    EventType.APP_CLOSED: ProgressPayload,
    EventType.FOCUS_STARTED: FocusStartPayload,
    EventType.FOCUS_ENDED: FocusEndPayload,
    EventType.USAGE_SUMMARY: SummaryPayload,
}


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,64}$")
    event_type: EventType
    occurred_at: datetime
    app_package: str | None = Field(default=None, pattern=PACKAGE_PATTERN)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return ensure_utc(v)

    @field_validator("event_type")
    @classmethod
    def _client_type(cls, v: EventType) -> EventType:
        if v not in CLIENT_EVENT_TYPES:
            raise ValueError("event type cannot be sent by clients")
        return v

    def parsed_payload(self) -> _Payload:
        return PAYLOADS[self.event_type].model_validate(self.payload)


EventResult = Literal["accepted", "duplicate", "rejected"]


async def ingest_event(session: AsyncSession, user_id: uuid.UUID, ev: ClientEvent, *, now: datetime, max_skew_s: int,
                       max_age_days: int) -> tuple[EventResult, str | None]:
    if ev.occurred_at > now + timedelta(seconds=max_skew_s):
        return "rejected", "occurred_at is in the future"
    if ev.occurred_at < now - timedelta(days=max_age_days):
        return "rejected", "event is too old"
    if ev.event_type not in (EventType.FOCUS_STARTED, EventType.FOCUS_ENDED, EventType.USAGE_SUMMARY) and not ev.app_package:
        return "rejected", "app_package is required"
    try:
        payload = ev.parsed_payload()
    except ValidationError as exc:
        return "rejected", f"invalid payload ({exc.error_count()} error(s))"
    exists = (await session.execute(select(Event.id).where(Event.user_id == user_id,
                                                           Event.client_event_id == ev.client_event_id))).first()
    if exists:
        return "duplicate", None
    usage_session = await _apply_to_session(session, user_id, ev, payload)
    row = Event(user_id=user_id, session_id=usage_session.id if usage_session else None, client_event_id=ev.client_event_id,
                event_type=ev.event_type.value, app_package=ev.app_package, occurred_at=ev.occurred_at, received_at=now,
                payload=payload.model_dump(mode="json"))
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return "duplicate", None
    return "accepted", None


async def _open_session(session: AsyncSession, user_id: uuid.UUID, package: str) -> UsageSession | None:
    return (
        await session.execute(
            select(UsageSession)
            .where(UsageSession.user_id == user_id, UsageSession.app_package == package, UsageSession.is_open.is_(True))
            .order_by(UsageSession.last_seen_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _category(package: str, hint: AppCategory | None) -> str:
    known = catalog.lookup(package)
    if known:
        return known.category.value
    return (hint or AppCategory.OTHER).value


async def _focus_active_at(session: AsyncSession, user_id: uuid.UUID, at: datetime) -> bool:
    last = (
        await session.execute(
            select(Event)
            .where(Event.user_id == user_id, Event.event_type.in_([EventType.FOCUS_STARTED.value, EventType.FOCUS_ENDED.value]),
                   Event.occurred_at <= at, Event.occurred_at >= at - timedelta(hours=6))
            .order_by(Event.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None or last.event_type != EventType.FOCUS_STARTED.value:
        return False
    planned = int((last.payload or {}).get("planned_minutes", 25))
    return ensure_utc(last.occurred_at) + timedelta(minutes=planned) > at


async def _apply_to_session(session: AsyncSession, user_id: uuid.UUID, ev: ClientEvent, payload: _Payload) -> UsageSession | None:
    if not ev.app_package:
        return None
    at = ev.occurred_at
    current = await _open_session(session, user_id, ev.app_package)
    if current is not None and at - ensure_utc(current.last_seen_at) > STALE_SESSION:
        current.is_open = False
        current.ended_at = current.last_seen_at
        current = None
    if ev.event_type in (EventType.APP_OPENED, EventType.SESSION_STARTED):
        hint = payload.app_category if isinstance(payload, OpenPayload) else None
        if current is not None and at - ensure_utc(current.last_seen_at) <= SESSION_GAP:
            current.open_count += 1
            current.last_seen_at = max(ensure_utc(current.last_seen_at), at)
            return current
        if current is not None:
            current.is_open, current.ended_at = False, current.last_seen_at
        created = UsageSession(user_id=user_id, app_package=ev.app_package, app_category=_category(ev.app_package, hint),
                               started_at=at, last_seen_at=at, duration_seconds=0, open_count=1, scroll_events=0, is_open=True, focus_mode=await _focus_active_at(session, user_id, at))
        session.add(created)
        await session.flush()
        return created
    if isinstance(payload, ProgressPayload):
        if current is None:
            started = at - timedelta(seconds=payload.duration_seconds)
            current = UsageSession(user_id=user_id, app_package=ev.app_package, app_category=_category(ev.app_package, None),
                                   started_at=started, last_seen_at=at, duration_seconds=0, open_count=1, scroll_events=0, is_open=True, focus_mode=await _focus_active_at(session, user_id, at))
            session.add(current)
        current.duration_seconds = max(current.duration_seconds, payload.duration_seconds,
                                       int((at - ensure_utc(current.started_at)).total_seconds()))
        current.scroll_events = max(current.scroll_events, payload.scroll_events)
        current.last_seen_at = max(ensure_utc(current.last_seen_at), at)
        if ev.event_type is EventType.APP_CLOSED:
            current.is_open = False
            current.ended_at = at
        await session.flush()
        return current
    return current


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    return start.astimezone(ZoneInfo("UTC")), (start + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _session_end(s: UsageSession) -> datetime:
    end = ensure_utc(s.ended_at or s.last_seen_at)
    by_duration = ensure_utc(s.started_at) + timedelta(seconds=s.duration_seconds)
    return max(end, by_duration)


async def sessions_between(session: AsyncSession, user_id: uuid.UUID, start: datetime, end: datetime) -> list[UsageSession]:
    return list(
        (
            await session.execute(
                select(UsageSession).where(UsageSession.user_id == user_id, UsageSession.started_at < end,
                                           UsageSession.last_seen_at >= start - timedelta(hours=6))
            )
        ).scalars()
    )


def clipped_seconds(s: UsageSession, start: datetime, end: datetime) -> float:
    lo, hi = max(ensure_utc(s.started_at), start), min(_session_end(s), end)
    return max(0.0, (hi - lo).total_seconds())


async def recompute_daily_features(session: AsyncSession, user_id: uuid.UUID, day: date, tz: ZoneInfo) -> BehaviorFeature:
    start, end = local_day_bounds(day, tz)
    sessions = await sessions_between(session, user_id, start, end)
    hourly = [0] * 24
    app_seconds: dict[str, int] = defaultdict(int)
    social = screen = productive = focus = night = 0.0
    counted = long_count = opens = 0
    max_session = 0.0
    for s in sessions:
        secs = clipped_seconds(s, start, end)
        if secs <= 0 and not (start <= ensure_utc(s.started_at) < end):
            continue
        screen += secs
        app_seconds[s.app_package] += int(secs)
        if s.focus_mode:
            focus += secs
        if s.app_category in PRODUCTIVE_APPS:
            productive += secs
        if s.app_category in SOCIAL:
            social += secs
            counted += 1
            opens += s.open_count
            max_session = max(max_session, secs)
            if secs >= LONG_SESSION_MINUTES * 60:
                long_count += 1
            cursor = max(ensure_utc(s.started_at), start)
            stop = min(_session_end(s), end)
            while cursor < stop:
                local_hour = cursor.astimezone(tz).hour
                step_end = min(stop, (cursor.astimezone(tz).replace(minute=0, second=0, microsecond=0)
                                      + timedelta(hours=1)).astimezone(ZoneInfo("UTC")))
                chunk = (step_end - cursor).total_seconds()
                hourly[local_hour] += int(chunk)
                if is_night_hour(local_hour):
                    night += chunk
                cursor = step_end
    interventions = (
        await session.execute(select(func.count()).select_from(Intervention).where(
            Intervention.user_id == user_id, Intervention.created_at >= start, Intervention.created_at < end,
            Intervention.final_decision != InterventionType.ALLOW.value))
    ).scalar_one()
    outcome_rows = (
        await session.execute(select(InterventionOutcome.outcome, func.count()).where(
            InterventionOutcome.user_id == user_id, InterventionOutcome.created_at >= start,
            InterventionOutcome.created_at < end).group_by(InterventionOutcome.outcome))
    ).all()
    outcomes = {o: n for o, n in outcome_rows}
    row = (await session.execute(select(BehaviorFeature).where(BehaviorFeature.user_id == user_id,
                                                               BehaviorFeature.feature_date == day))).scalar_one_or_none()
    if row is None:
        row = BehaviorFeature(user_id=user_id, feature_date=day)
        session.add(row)
    row.total_social_seconds, row.total_screen_seconds, row.productive_seconds = int(social), int(screen), int(productive)
    row.sessions_count, row.long_sessions_count, row.max_session_seconds = counted, long_count, int(max_session)
    row.opens_count, row.focus_seconds, row.night_social_seconds = opens, int(focus), int(night)
    row.interventions_count = int(interventions)
    row.accepted_count = int(outcomes.get(OutcomeType.ACCEPTED.value, 0) + outcomes.get(OutcomeType.STOPPED_SESSION.value, 0)
                             + outcomes.get(OutcomeType.RETURNED_TO_TASK.value, 0))
    row.overridden_count = int(outcomes.get(OutcomeType.OVERRIDDEN.value, 0) + outcomes.get(OutcomeType.DISABLED_PROTECTION.value, 0))
    row.hourly_social_seconds = hourly
    row.app_seconds = dict(app_seconds)
    await session.flush()
    return row


async def build_context(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime,
    tz: ZoneInfo,
    app_package: str | None,
    goals: list[GoalSnapshot],
    daily_limit_minutes: int | None,
    session_minutes_hint: float | None = None,
) -> ContextSnapshot:
    local = now.astimezone(tz)
    app = catalog.lookup(app_package)
    category = app.category if app else AppCategory.OTHER
    current = await _open_session(session, user_id, app_package) if app_package else None
    if current is not None and now - ensure_utc(current.last_seen_at) > STALE_SESSION:
        current = None
    if current is not None and not app:
        category = AppCategory(current.app_category)
    session_minutes = 0.0
    if current is not None:
        session_minutes = max(current.duration_seconds, (ensure_utc(current.last_seen_at) - ensure_utc(current.started_at)).total_seconds()) / 60
    if session_minutes_hint is not None:
        session_minutes = max(session_minutes, min(session_minutes_hint, 24 * 60))
    opens = (
        await session.execute(select(func.count()).select_from(Event).where(
            Event.user_id == user_id, Event.event_type == EventType.APP_OPENED.value, Event.occurred_at >= now - timedelta(hours=1),
            Event.occurred_at <= now + timedelta(minutes=5)))
    ).scalar_one()
    day_start, day_end = local_day_bounds(local.date(), tz)
    by_app: dict[str, float] = defaultdict(float)
    by_category: dict[str, float] = defaultdict(float)
    for s in await sessions_between(session, user_id, day_start, day_end):
        minutes = clipped_seconds(s, day_start, min(day_end, now + timedelta(minutes=1))) / 60
        by_app[s.app_package] += minutes
        by_category[s.app_category] += minutes
    since = min(day_start, ensure_utc(current.started_at)) if current is not None else day_start
    recent_all = list((await session.execute(select(Intervention).where(
        Intervention.user_id == user_id, Intervention.created_at >= since,
        Intervention.final_decision != InterventionType.ALLOW.value).order_by(Intervention.created_at.desc()))).scalars())
    recent = [i for i in recent_all if ensure_utc(i.created_at) >= day_start]
    last = recent_all[0].created_at if recent_all else None
    # A session that spans midnight keeps its escalation state: look back to the session start, not the day start.
    warned = current is not None and any(
        i.app_package == app_package and ensure_utc(i.created_at) >= ensure_utc(current.started_at) for i in recent_all
    )
    avg_recent_risk = (await session.execute(select(func.avg(RiskScore.distraction_risk)).where(
        RiskScore.user_id == user_id, RiskScore.created_at >= now - timedelta(days=7)))).scalar_one()
    history: Literal["low", "medium", "high", "unknown"] = "unknown"
    if avg_recent_risk is not None:
        history = "high" if avg_recent_risk >= 0.6 else "medium" if avg_recent_risk >= 0.4 else "low"
    return ContextSnapshot(
        now_utc=now,
        local_time=local.strftime("%H:%M"),
        timezone=str(tz),
        weekday=local.weekday(),
        minute_of_day=local.hour * 60 + local.minute,
        is_night=is_night_hour(local.hour),
        is_weekend=local.weekday() >= 5,
        app_package=app_package,
        app_name=app.name if app else app_package,
        app_category=category,
        session_id=current.id if current else None,
        session_minutes=round(session_minutes, 2),
        scroll_events=current.scroll_events if current else 0,
        opens_last_hour=int(opens),
        daily_social_minutes=round(sum(v for k, v in by_category.items() if k in SOCIAL), 2),
        daily_minutes_by_app={k: round(v, 2) for k, v in by_app.items()},
        daily_minutes_by_category={k: round(v, 2) for k, v in by_category.items()},
        daily_limit_minutes=daily_limit_minutes,
        focus_session_active=await _focus_active_at(session, user_id, now),
        active_goals=goals,
        minutes_since_last_intervention=round((now - ensure_utc(last)).total_seconds() / 60, 2) if last else None,
        warned_this_session=warned,
        interventions_today=len(recent),
        hard_interventions_today=sum(1 for i in recent if InterventionType(i.final_decision) in HARD_INTERVENTIONS),
        risk_history=history,
    )


async def recent_usage(session: AsyncSession, user_id: uuid.UUID, *, now: datetime, hours: int) -> list[dict[str, Any]]:
    rows = await sessions_between(session, user_id, now - timedelta(hours=hours), now + timedelta(minutes=1))
    rows.sort(key=lambda s: ensure_utc(s.started_at), reverse=True)
    return [
        {"app_package": s.app_package, "app_category": s.app_category, "started_at": ensure_utc(s.started_at).isoformat(),
         "minutes": round(clipped_seconds(s, now - timedelta(hours=hours), now + timedelta(minutes=1)) / 60, 1),
         "open_count": s.open_count, "focus_mode": s.focus_mode, "is_open": s.is_open}
        for s in rows[:100]
    ]
