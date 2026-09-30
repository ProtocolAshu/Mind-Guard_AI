"""Observability records for every run (section 31): agent_runs, agent_steps,
tool_calls and llm_calls.

PostgreSQL: each record is committed in its own short transaction, so traces survive
a failed request and are visible to the admin dashboard while a run is in flight.
SQLite (dev/test) allows a single writer, so records share the request session.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import AgentRun, AgentStep, LLMCall, ToolCall
from app.observability import metrics
from app.providers.gateway import LLMCallRecord
from app.schemas.common import RunStatus, RunType
from app.services.llm_accounting import record_to_row

_REDACT_KEYS = {"ticket", "text", "password", "token", "access_token", "refresh_token"}
MAX_SUMMARY_CHARS = 4000


def summarize(value: Any, depth: int = 0) -> Any:
    """Bounded, redacted, JSON-safe summary of a payload for traces (never raw content or secrets)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:40]:
            if k in _REDACT_KEYS and v is not None:
                out[k] = f"<redacted:{len(str(v))} chars>"
            elif depth >= 3:
                out[k] = "…"
            else:
                out[k] = summarize(v, depth + 1)
        return out
    if isinstance(value, list | tuple):
        items = [summarize(v, depth + 1) for v in list(value)[:20]]
        if len(value) > 20:
            items.append(f"… {len(value) - 20} more")
        return items
    if isinstance(value, str) and len(value) > 300:
        return value[:297] + "…"
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, uuid.UUID | datetime):
        return str(value)
    return value


def _bounded(value: Any) -> dict[str, Any]:
    summary = summarize(value)
    if not isinstance(summary, dict):
        summary = {"value": summary}
    text = json.dumps(summary, default=str)
    if len(text) > MAX_SUMMARY_CHARS:
        return {"truncated": True, "keys": list(summary)[:40]}
    return summary


class RunRecorder:
    def __init__(self, session: AsyncSession, factory: async_sessionmaker[AsyncSession] | None = None):
        self.session = session
        self.factory = factory if factory is not None and session.get_bind().dialect.name == "postgresql" else None
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.tool_calls = 0

    async def _add(self, obj: Any) -> None:
        if self.factory is not None:
            async with self.factory() as s:
                s.add(obj)
                await s.commit()
        else:
            self.session.add(obj)
            await self.session.flush()

    async def start_run(self, *, user_id: uuid.UUID | None, run_type: RunType, trigger: dict[str, Any], now: datetime) -> uuid.UUID:
        run = AgentRun(id=uuid.uuid4(), user_id=user_id, run_type=run_type.value, status=RunStatus.RUNNING.value,
                       trigger=_bounded(trigger), started_at=now)
        await self._add(run)
        return run.id

    async def step(self, *, run_id: uuid.UUID, seq: int, node: str, status: str, latency_ms: float, started_at: datetime,
                   input_summary: Any, output_summary: Any, error: str | None) -> None:
        metrics.AGENT_STEP_LATENCY.labels(node, status).observe(latency_ms / 1000)
        await self._add(AgentStep(run_id=run_id, seq=seq, node=node, status=status, started_at=started_at,
                                  latency_ms=round(latency_ms, 3), input_summary=_bounded(input_summary),
                                  output_summary=_bounded(output_summary), error=error))

    async def tool_call(self, *, run_id: uuid.UUID | None, user_id: uuid.UUID | None, tool_name: str, arguments: Any,
                        result: Any, status: str, error: str | None, latency_ms: float) -> None:
        self.tool_calls += 1
        await self._add(ToolCall(run_id=run_id, user_id=user_id, tool_name=tool_name, arguments=_bounded(arguments),
                                 result=_bounded(result) if result is not None else None, status=status, error=error,
                                 latency_ms=round(latency_ms, 3), created_at=datetime.now(UTC)))

    async def llm_call(self, record: LLMCallRecord) -> None:
        self.llm_calls += 1
        self.tokens_in += record.prompt_tokens
        self.tokens_out += record.completion_tokens
        self.cost_usd += record.cost_usd
        row: LLMCall = record_to_row(record)
        await self._add(row)

    async def finish_run(self, run_id: uuid.UUID, *, run_type: RunType, status: RunStatus, path: list[str],
                         final_decision: str | None, error: str | None, latency_ms: float, now: datetime) -> None:
        metrics.AGENT_RUNS.labels(run_type.value, status.value).inc()
        metrics.AGENT_RUN_LATENCY.labels(run_type.value).observe(latency_ms / 1000)
        values = dict(status=status.value, path=path, final_decision=final_decision, error=(error or None) and error[:2000],
                      ended_at=now, latency_ms=round(latency_ms, 3), llm_calls=self.llm_calls, tokens_in=self.tokens_in,
                      tokens_out=self.tokens_out, cost_usd=round(self.cost_usd, 8))
        stmt = update(AgentRun).where(AgentRun.id == run_id).values(**values)
        if self.factory is not None:
            async with self.factory() as s:
                await s.execute(stmt)
                await s.commit()
        else:
            await self.session.execute(stmt)
