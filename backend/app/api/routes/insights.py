from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_current_user, get_now, get_session
from app.container import Container
from app.core.errors import ConsentRequiredError, ValidationFailedError
from app.database.models import User
from app.memory.service import MemoryService
from app.rag.knowledge import search_knowledge
from app.schemas.agents import MemoryHit
from app.schemas.api import MemoryCreate, MemoryOut, MemorySearch
from app.schemas.common import ConsentScope, MemorySource, MemoryType
from app.security.audit import append_audit
from app.services import analytics
from app.services.privacy import delete_user_data, export_user_data
from app.services.users import get_preferences, granted_scopes, user_timezone

router = APIRouter(tags=["Analytics", "Memory", "Privacy"])


@router.get("/api/analytics/daily")
async def analytics_daily(day: date | None = Query(default=None, alias="date"), user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> dict[str, object]:
    prefs = await get_preferences(session, user.id)
    tz = user_timezone(prefs)
    return await analytics.daily(session, user.id, day or now.astimezone(tz).date(), prefs, tz, now)


@router.get("/api/analytics/weekly")
async def analytics_weekly(end: date | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                           now: datetime = Depends(get_now)) -> dict[str, object]:
    prefs = await get_preferences(session, user.id)
    tz = user_timezone(prefs)
    return await analytics.weekly(session, user.id, end or now.astimezone(tz).date(), prefs, tz, now)


@router.get("/api/analytics/trends")
async def analytics_trends(days: int = Query(default=30, ge=7, le=90), user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> dict[str, object]:
    prefs = await get_preferences(session, user.id)
    return await analytics.trends(session, user.id, days, prefs, user_timezone(prefs), now)


@router.get("/api/analytics/insights")
async def analytics_insights(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                             now: datetime = Depends(get_now)) -> list[dict[str, object]]:
    return await analytics.insights(session, user.id, now)


@router.get("/api/memory", response_model=list[MemoryOut])
async def list_memory(memory_type: MemoryType | None = Query(default=None, alias="type"), limit: int = Query(default=50, ge=1, le=500),
                      offset: int = Query(default=0, ge=0), user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), container: Container = Depends(get_container),
                      now: datetime = Depends(get_now)) -> list[MemoryOut]:
    rows = await MemoryService(session, container.deps.embedder).list(user.id, memory_type=memory_type, limit=limit, offset=offset, now=now)
    return [MemoryOut.model_validate(r) for r in rows]


@router.post("/api/memory", response_model=MemoryOut, status_code=201)
async def add_memory(body: MemoryCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                     container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> MemoryOut:
    if ConsentScope.MEMORY_PERSONALIZATION not in await granted_scopes(session, user.id):
        raise ConsentRequiredError("memory personalization consent is required", code="consent_required")
    memory = await MemoryService(session, container.deps.embedder).add(user.id, MemoryType.SEMANTIC, body.content, now=now,
                                                                       importance=body.importance, source=MemorySource.USER)
    return MemoryOut.model_validate(memory)


@router.post("/api/memory/search", response_model=list[MemoryHit])
async def memory_search(body: MemorySearch, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> list[MemoryHit]:
    return await MemoryService(session, container.deps.embedder).retrieve(user.id, body.query, now=now, k=body.k)


@router.delete("/api/memory/{memory_id}", status_code=204)
async def delete_memory(memory_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> None:
    await MemoryService(session, container.deps.embedder).delete(user.id, memory_id)
    await append_audit(session, actor=f"user:{user.id}", action="memory.deleted", resource_type="memory", resource_id=memory_id,
                       details={}, user_id=user.id, now=now)


@router.get("/api/user/export")
async def export_data(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      now: datetime = Depends(get_now)) -> JSONResponse:
    data = await export_user_data(session, user.id)
    await append_audit(session, actor=f"user:{user.id}", action="user.data_exported", resource_type="user", resource_id=user.id,
                       details={"tables": len(data["tables"])}, user_id=user.id, now=now)
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="mindguard-export.json"'})


@router.delete("/api/user/data")
async def delete_data(scope: Literal["history", "all"] = "history", confirm: str = Query(default=""),
                      user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      now: datetime = Depends(get_now)) -> dict[str, object]:
    if scope == "all" and confirm != "DELETE":
        raise ValidationFailedError("deleting the account requires confirm=DELETE", code="confirmation_required")
    counts = await delete_user_data(session, user.id, scope, actor=f"user:{user.id}", now=now)
    return {"scope": scope, "deleted": counts}


@router.get("/api/knowledge/search", tags=["Knowledge"])
async def knowledge(q: str = Query(min_length=2, max_length=300), k: int = Query(default=3, ge=1, le=10),
                    _: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                    container: Container = Depends(get_container)) -> list[dict[str, object]]:
    hits = await search_knowledge(session, container.deps.embedder, q, k=k)
    return [{"slug": h.slug, "title": h.title, "chunk": h.chunk_index, "content": h.content, "similarity": h.similarity, "kind": h.kind}
            for h in hits]
