from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_current_user, get_now, get_session
from app.container import Container
from app.core.clock import ensure_utc
from app.database.models import UsageSession, User
from app.observability import metrics
from app.schemas.api import EventBatch, EventBatchResult, RejectedEvent
from app.services.usage import ClientEvent, clipped_seconds, ingest_event, recent_usage, recompute_daily_features
from app.services.users import get_preferences, user_timezone

router = APIRouter(tags=["Usage", "Sessions"])


@router.post("/api/events", response_model=EventBatchResult)
async def post_events(body: EventBatch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> EventBatchResult:
    accepted = duplicates = 0
    rejected: list[RejectedEvent] = []
    tz = user_timezone(await get_preferences(session, user.id))
    dates = set()
    for index, raw in enumerate(body.events):
        cid = raw.get("client_event_id") if isinstance(raw.get("client_event_id"), str) else None
        try:
            event = ClientEvent.model_validate(raw)
        except ValidationError as exc:
            rejected.append(RejectedEvent(index=index, client_event_id=cid, reason=f"invalid event ({exc.error_count()} error(s))"))
            metrics.EVENTS_INGESTED.labels(str(raw.get("event_type", "unknown"))[:32], "rejected").inc()
            continue
        result, reason = await ingest_event(session, user.id, event, now=now,
                                            max_skew_s=container.settings.event_max_clock_skew_seconds,
                                            max_age_days=container.settings.event_max_age_days)
        metrics.EVENTS_INGESTED.labels(event.event_type.value, result).inc()
        if result == "accepted":
            accepted += 1
            dates.add(event.occurred_at.astimezone(tz).date())
        elif result == "duplicate":
            duplicates += 1
        else:
            rejected.append(RejectedEvent(index=index, client_event_id=cid, reason=reason or "rejected"))
    for day in sorted(dates)[-8:]:
        await recompute_daily_features(session, user.id, day, tz)
    return EventBatchResult(accepted=accepted, duplicates=duplicates, rejected=rejected)


@router.get("/api/usage/recent")
async def usage_recent(hours: int = Query(default=24, ge=1, le=72), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> dict[str, object]:
    items = await recent_usage(session, user.id, now=now, hours=hours)
    return {"hours": hours, "total_minutes": round(sum(float(i["minutes"]) for i in items), 1), "sessions": items}


@router.get("/api/sessions")
async def list_sessions(days: int = Query(default=7, ge=1, le=90), limit: int = Query(default=200, ge=1, le=1000),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        now: datetime = Depends(get_now)) -> list[dict[str, object]]:
    since = now - timedelta(days=days)
    rows = (await session.execute(select(UsageSession).where(UsageSession.user_id == user.id, UsageSession.started_at >= since)
                                  .order_by(UsageSession.started_at.desc()).limit(limit))).scalars()
    return [{"id": str(s.id), "app_package": s.app_package, "app_category": s.app_category,
             "started_at": ensure_utc(s.started_at).isoformat(),
             "ended_at": ensure_utc(s.ended_at).isoformat() if s.ended_at else None,
             "minutes": round(clipped_seconds(s, since, now + timedelta(minutes=1)) / 60, 1), "open_count": s.open_count,
             "scroll_events": s.scroll_events, "focus_mode": s.focus_mode, "is_open": s.is_open} for s in rows]
