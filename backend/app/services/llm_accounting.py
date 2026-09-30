"""Persist LLM call accounting and answer budget questions for the gateway."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import LLMCall
from app.providers.gateway import LLMCallRecord


def record_to_row(record: LLMCallRecord) -> LLMCall:
    return LLMCall(run_id=record.run_id, user_id=record.user_id, provider=record.provider, model=record.model,
                   purpose=record.purpose, prompt_tokens=record.prompt_tokens, completion_tokens=record.completion_tokens,
                   latency_ms=record.latency_ms, cost_usd=record.cost_usd, cache_hit=record.cache_hit,
                   status=record.status.value, error=record.error, created_at=record.created_at)


def make_usage_lookup(session: AsyncSession, now: Callable[[], datetime]) -> Callable[[uuid.UUID | None], Awaitable[tuple[int, float]]]:
    async def lookup(user_id: uuid.UUID | None) -> tuple[int, float]:
        current = now().astimezone(UTC)
        day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        tokens = 0
        if user_id is not None:
            tokens = (await session.execute(select(func.coalesce(func.sum(LLMCall.prompt_tokens + LLMCall.completion_tokens), 0))
                                            .where(LLMCall.user_id == user_id, LLMCall.created_at >= day))).scalar_one()
        cost = (await session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                      .where(LLMCall.created_at >= month))).scalar_one()
        return int(tokens), float(cost)

    return lookup
