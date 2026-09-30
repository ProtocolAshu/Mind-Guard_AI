from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import PROMPTS, system_prompt
from app.api.deps import get_container, get_current_user, get_now, get_session, require_admin
from app.container import Container
from app.core.errors import NotFoundError
from app.database.models import AgentRun, AgentStep, Intervention, LLMCall, ModelVersion, ToolCall, User
from app.observability.metrics import REGISTRY
from app.security.audit import verify_audit_chain

router = APIRouter()


@router.get("/health", tags=["System"])
async def health(container: Container = Depends(get_container)) -> dict[str, str]:
    return {"status": "ok", "version": container.settings.api_version}


@router.get("/ready", tags=["System"])
async def ready(container: Container = Depends(get_container)) -> PlainTextResponse:
    checks: dict[str, str] = {}
    try:
        async with container.session_factory() as s:
            await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    if container.settings.redis_url:
        try:
            await container.redis.ping() if container.redis is not None else (_ for _ in ()).throw(RuntimeError())
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "degraded"
    ok = checks["database"] == "ok"
    body = ",".join(f"{k}={v}" for k, v in checks.items())
    return PlainTextResponse(body, status_code=200 if ok else 503)


@router.get("/metrics", tags=["System"], include_in_schema=False)
async def prometheus(request: Request, container: Container = Depends(get_container)) -> PlainTextResponse:
    token = container.settings.metrics_token.get_secret_value()
    if token:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
            raise NotFoundError("not found")
    elif container.settings.environment == "production":
        raise NotFoundError("not found")
    return PlainTextResponse(generate_latest(REGISTRY).decode(), media_type=CONTENT_TYPE_LATEST)


def _run(r: AgentRun) -> dict[str, Any]:
    return {"id": str(r.id), "user_id": str(r.user_id) if r.user_id else None, "run_type": r.run_type, "status": r.status,
            "final_decision": r.final_decision, "path": r.path, "error": r.error, "started_at": r.started_at.isoformat(),
            "latency_ms": r.latency_ms, "llm_calls": r.llm_calls, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
            "cost_usd": r.cost_usd}


async def _run_detail(session: AsyncSession, run: AgentRun) -> dict[str, Any]:
    steps = (await session.execute(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.seq))).scalars()
    tools = (await session.execute(select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at))).scalars()
    llm = (await session.execute(select(LLMCall).where(LLMCall.run_id == run.id).order_by(LLMCall.created_at))).scalars()
    return {**_run(run), "trigger": run.trigger,
            "steps": [{"seq": s.seq, "node": s.node, "status": s.status, "latency_ms": s.latency_ms, "input": s.input_summary,
                       "output": s.output_summary, "error": s.error} for s in steps],
            "tool_calls": [{"tool": t.tool_name, "status": t.status, "latency_ms": t.latency_ms, "arguments": t.arguments,
                            "result": t.result, "error": t.error} for t in tools],
            "model_calls": [{"provider": c.provider, "model": c.model, "purpose": c.purpose, "status": c.status,
                           "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens, "latency_ms": c.latency_ms,
                           "cost_usd": c.cost_usd, "cache_hit": c.cache_hit, "error": c.error} for c in llm]}


@router.get("/api/agent/runs", tags=["Agent status"])
async def my_runs(limit: int = Query(default=50, ge=1, le=500), run_type: str | None = None, user: User = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.started_at.desc()).limit(limit)
    if run_type:
        stmt = stmt.where(AgentRun.run_type == run_type)
    return [_run(r) for r in (await session.execute(stmt)).scalars()]


@router.get("/api/agent/runs/{run_id}", tags=["Agent status"])
async def my_run(run_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = (await session.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))).scalar_one_or_none()
    if run is None:
        raise NotFoundError("run not found")
    return await _run_detail(session, run)


@router.get("/api/agent/prompts", tags=["Agent status"])
async def prompts(_: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [{"agent": name, "runtime_llm": p.runtime_llm, "prompt": system_prompt(name)} for name, p in PROMPTS.items()]


@router.get("/api/agent/tools", tags=["Agent status"])
async def tools(_: User = Depends(get_current_user), container: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return container.deps.tools.describe()


@router.get("/api/agent/status", tags=["Agent status"])
async def agent_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                       container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> dict[str, Any]:
    graph = container.orchestrator.evaluation_graph.get_graph()
    rows = (await session.execute(select(AgentRun.status, func.count()).where(AgentRun.user_id == user.id,
                                                                            AgentRun.started_at >= now - timedelta(hours=24))
                                  .group_by(AgentRun.status))).all()
    return {"graph": {"nodes": sorted(n for n in graph.nodes if not n.startswith("__")),
                      "edges": [{"source": e.source, "target": e.target, "conditional": e.conditional} for e in graph.edges]},
            "runs_last_24h": {s: n for s, n in rows}, "llm_enabled": container.deps.llm_provider is not None,
            "llm_provider": container.deps.llm_provider.name if container.deps.llm_provider else "disabled"}


@router.get("/api/models/status", tags=["Model status"])
async def model_status(_: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                       container: Container = Depends(get_container)) -> dict[str, Any]:
    deps, s = container.deps, container.settings
    versions = (await session.execute(select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(50))).scalars()
    return {
        "risk": deps.risk_scorer.describe(),
        "policy_engine": deps.policy_engine.name,
        "intervention_controller": deps.interventions.name,
        "content_classifier": deps.classifier.name,
        "llm": {"provider": deps.llm_provider.name if deps.llm_provider else "disabled", "fast_model": s.llm_fast_model,
                "reasoning_model": s.llm_reasoning_model, "fallback_model": s.llm_fallback_model,
                "daily_token_budget_per_user": s.llm_user_daily_token_budget, "monthly_budget_usd": s.llm_monthly_budget_usd},
        "vision": {"provider": deps.vision.name if deps.vision else "disabled", "model": s.vision_model},
        "embeddings": {"provider": deps.embedder.name, "model": deps.embedder.model_name, "dim": deps.embedder.dim},
        "bandit": {"algorithm": s.bandit_algorithm},
        "registered_versions": [{"model_name": v.model_name, "version": v.version, "kind": v.kind, "metrics": v.metrics,
                                 "is_active": v.is_active, "trained_at": v.trained_at.isoformat() if v.trained_at else None}
                                for v in versions],
    }


@router.get("/api/admin/runs", tags=["Admin"])
async def admin_runs(limit: int = Query(default=100, ge=1, le=1000), status: str | None = None, _: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    return [_run(r) for r in (await session.execute(stmt)).scalars()]


@router.get("/api/admin/runs/{run_id}", tags=["Admin"])
async def admin_run(run_id: uuid.UUID, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError("run not found")
    return await _run_detail(session, run)


@router.post("/api/admin/runs/{run_id}/replay", tags=["Admin"])
async def admin_replay(run_id: uuid.UUID, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
                       container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> dict[str, Any]:
    """Debug replay: re-run a recorded evaluation for its user *now* inside a SAVEPOINT that is rolled back, so no
    intervention, risk score or memory is persisted. Raw content text is never stored, so replays use app metadata only."""
    from app.schemas.common import RunType
    from app.schemas.evaluation import EvaluateRequest

    run = await session.get(AgentRun, run_id)
    if run is None or run.user_id is None or run.run_type not in ("evaluate", "replay"):
        raise NotFoundError("replayable evaluation run not found")
    trigger = {k: v for k, v in (run.trigger or {}).items() if k in ("app_package", "session_minutes", "device_capabilities")}
    original = _run(run)
    savepoint = await session.begin_nested()
    try:
        replay = await container.orchestrator.evaluate(session, run.user_id, EvaluateRequest.model_validate(trigger), now,
                                                       run_type=RunType.REPLAY)
    finally:
        await savepoint.rollback()
    return {"original": {"final_decision": original["final_decision"], "path": original["path"], "status": original["status"],
                         "started_at": original["started_at"]},
            "replay": {"decision": replay.decision, "proposed": replay.proposed, "path": replay.path, "reason_codes": replay.reason_codes,
                       "guardrail_flags": replay.guardrail_flags, "explanation": replay.explanation,
                       "explanation_points": replay.explanation_points, "risk": replay.risk, "degraded": replay.degraded,
                       "notes": replay.notes},
            "dry_run": True, "content_replayed": False,
            "decision_changed": original["final_decision"] != replay.decision.value}


@router.get("/api/admin/users", tags=["Admin"])
async def admin_users(synthetic: bool = False, limit: int = Query(default=100, ge=1, le=1000), _: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if synthetic:
        stmt = stmt.where(User.email.like("%@synthetic.mindguard.dev"))
    users = (await session.execute(stmt)).scalars().all()
    counts: dict[uuid.UUID | None, int] = {
        row[0]: int(row[1]) for row in (await session.execute(select(AgentRun.user_id, func.count()).group_by(AgentRun.user_id))).all()
    }
    return [{"id": str(u.id), "email": u.email, "display_name": u.display_name, "role": u.role, "created_at": u.created_at.isoformat(),
             "runs": counts.get(u.id, 0), "synthetic": u.email.endswith("@synthetic.mindguard.dev")} for u in users]


@router.get("/api/admin/audit/verify", tags=["Admin"])
async def audit_verify(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await verify_audit_chain(session)
    return {"valid": result.valid, "checked": result.checked, "first_invalid_seq": result.first_invalid_seq, "reason": result.reason}


@router.get("/api/admin/stats", tags=["Admin"])
async def admin_stats(days: int = Query(default=7, ge=1, le=90), _: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session), container: Container = Depends(get_container),
                      now: datetime = Depends(get_now)) -> dict[str, Any]:
    since = now - timedelta(days=days)
    month_start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_to_date = float((await session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                                 .where(LLMCall.created_at >= month_start))).scalar_one())
    window_spend = float((await session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                                .where(LLMCall.created_at >= since))).scalar_one())
    budget = container.settings.llm_monthly_budget_usd
    runs = (await session.execute(select(AgentRun.run_type, AgentRun.status, func.count(), func.avg(AgentRun.latency_ms))
                                  .where(AgentRun.started_at >= since).group_by(AgentRun.run_type, AgentRun.status))).all()
    llm = (await session.execute(select(LLMCall.provider, LLMCall.model, LLMCall.purpose, LLMCall.status, func.count(),
                                        func.sum(LLMCall.prompt_tokens), func.sum(LLMCall.completion_tokens), func.sum(LLMCall.cost_usd),
                                        func.avg(LLMCall.latency_ms))
                                 .where(LLMCall.created_at >= since)
                                 .group_by(LLMCall.provider, LLMCall.model, LLMCall.purpose, LLMCall.status))).all()
    decisions = (await session.execute(select(Intervention.final_decision, func.count()).where(Intervention.created_at >= since)
                                       .group_by(Intervention.final_decision))).all()
    flags: dict[str, int] = {}
    for (row_flags,) in (await session.execute(select(Intervention.guardrail_flags).where(Intervention.created_at >= since))).all():
        for flag in row_flags or []:
            flags[flag] = flags.get(flag, 0) + 1
    tool_failures = (await session.execute(select(ToolCall.tool_name, ToolCall.status, func.count()).where(
        ToolCall.created_at >= since, ToolCall.status != "ok").group_by(ToolCall.tool_name, ToolCall.status))).all()
    return {
        "users": (await session.execute(select(func.count()).select_from(User))).scalar_one(),
        "runs": [{"run_type": t, "status": s, "count": n, "avg_latency_ms": round(float(lat or 0), 2)} for t, s, n, lat in runs],
        "llm_usage": [{"provider": p, "model": m, "purpose": pu, "status": st, "calls": n, "prompt_tokens": int(pt or 0),
                       "completion_tokens": int(ct or 0), "cost_usd": round(float(c or 0), 6), "avg_latency_ms": round(float(lat or 0), 2)}
                      for p, m, pu, st, n, pt, ct, c, lat in llm],
        "decisions": {d: n for d, n in decisions},
        "guardrail_flags": dict(sorted(flags.items(), key=lambda kv: -kv[1])),
        "tool_failures": [{"tool": t, "status": s, "count": n} for t, s, n in tool_failures],
        "cost": {"window_days": days, "window_spend_usd": round(window_spend, 6),
                 "projected_monthly_usd": round(window_spend / days * 30, 4), "month_to_date_usd": round(month_to_date, 6),
                 "monthly_budget_usd": budget, "budget_used_pct": round(100 * month_to_date / budget, 2) if budget > 0 else None,
                 "note": "Estimated from recorded token counts and MODEL_PRICING_JSON; not an invoice."},
    }
