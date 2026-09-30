"""Learning Agent (section 4.10): outcome -> reward -> bandit update -> episodic memory ->
statistically-supported preference -> (optional) LLM reflection validated against the
statistics -> policy suggestions that the user must accept. Nothing here changes a policy.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.agents.prompts import system_prompt
from app.agents.runtime import RunContext
from app.core.clock import ensure_utc
from app.core.errors import require
from app.database.models import Intervention, InterventionOutcome, Memory
from app.policies import catalog
from app.providers.base import ChatMessage, LLMRequest
from app.schemas.common import (
    INTERVENTION_SEVERITY,
    ConsentScope,
    InterventionType,
    MemoryType,
    OutcomeType,
)
from app.schemas.llm import LearningReflectionOutput
from app.services.users import user_timezone

NEGATIVE = {OutcomeType.OVERRIDDEN.value, OutcomeType.DISABLED_PROTECTION.value}
HARD = (InterventionType.TEMPORARY_BLOCK, InterventionType.LIMITED_ACCESS)


class LearningError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class LearningResult:
    intervention_id: str
    outcome: str
    reward: float
    decision: str
    bandit_updated: bool
    memory_written: bool = False
    preference: dict[str, Any] | None = None
    suggestion: dict[str, Any] | None = None
    insights: list[str] = field(default_factory=list)


async def outcome_stats(ctx: RunContext, days: int = 30) -> dict[str, dict[str, float]]:
    rows = (
        await ctx.session.execute(
            select(Intervention.final_decision, InterventionOutcome.outcome, InterventionOutcome.reward)
            .join(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
            .where(Intervention.user_id == ctx.user_id, Intervention.created_at >= ctx.now - timedelta(days=days))
        )
    ).all()
    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "reward_sum": 0.0, "overrides": 0})
    for decision, outcome, reward in rows:
        agg[decision]["n"] += 1
        agg[decision]["reward_sum"] += float(reward)
        agg[decision]["overrides"] += outcome in NEGATIVE
    return {d: {"n": v["n"], "mean_reward": round(v["reward_sum"] / v["n"], 3), "overrides": v["overrides"]}
            for d, v in agg.items() if v["n"]}


def extract_preference(stats: dict[str, dict[str, float]]) -> dict[str, Any] | None:
    for hard in HARD:
        s = stats.get(hard.value)
        if not s or s["n"] < 2 or s["overrides"] / s["n"] < 0.5:
            continue
        gentler = [
            (InterventionType(d), v) for d, v in stats.items()
            if d != InterventionType.ALLOW.value and INTERVENTION_SEVERITY[InterventionType(d)] < INTERVENTION_SEVERITY[hard]
            and v["n"] >= 2 and v["mean_reward"] >= 0.5
        ]
        if gentler:
            best, v = max(gentler, key=lambda item: (item[1]["mean_reward"], -INTERVENTION_SEVERITY[item[0]]))
            return {"preferred": best.value, "avoid": hard.value,
                    "evidence": f"overrode {int(s['overrides'])}/{int(s['n'])} {hard.value}; "
                                f"{best.value} mean reward {v['mean_reward']:+.2f} over {int(v['n'])}"}
    solid = [(d, v) for d, v in stats.items() if d != InterventionType.ALLOW.value and v["n"] >= 3 and v["mean_reward"] >= 0.7]
    if solid:
        d, v = max(solid, key=lambda item: item[1]["mean_reward"])
        return {"preferred": d, "avoid": None, "evidence": f"{d} mean reward {v['mean_reward']:+.2f} over {int(v['n'])}"}
    return None


def validate_reflection(reflection: LearningReflectionOutput, stats: dict[str, dict[str, float]]) -> bool:
    """An LLM claim is kept only if the raw numbers say the same thing."""
    tried = {d: v for d, v in stats.items() if v["n"] >= 2}
    if reflection.preferred_intervention is None or len(tried) < 2:
        return False
    best = max(tried, key=lambda d: tried[d]["mean_reward"])
    if reflection.preferred_intervention.value != best:
        return False
    if reflection.avoid_intervention is not None:
        worst = min(tried, key=lambda d: tried[d]["mean_reward"])
        return reflection.avoid_intervention.value == worst
    return True


async def learn(ctx: RunContext, *, intervention_id: str, outcome: OutcomeType, satisfaction: int | None) -> LearningResult:
    tools = ctx.deps.tools
    logged = await tools.invoke("log_outcome", {"intervention_id": intervention_id, "outcome": outcome.value,
                                                "satisfaction": satisfaction}, ctx)
    if not logged.ok or logged.output is None:
        raise LearningError(logged.status.value, logged.error or "outcome could not be recorded")
    out = logged.output.model_dump(mode="json")
    result = LearningResult(intervention_id=out["intervention_id"], outcome=out["outcome"], reward=out["reward"],
                            decision=out["decision"], bandit_updated=out["bandit_updated"])
    intervention = require(await ctx.session.get(Intervention, logged.output.intervention_id),  # type: ignore[attr-defined]
                           "intervention missing for a logged outcome")
    if ctx.has(ConsentScope.MEMORY_PERSONALIZATION):
        local = ensure_utc(intervention.created_at).astimezone(user_timezone(ctx.prefs))
        app = catalog.lookup(intervention.app_package)
        hour = local.hour
        bucket = "late night" if hour >= 22 or hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        content = (f"{app.name if app else intervention.app_package} {bucket} session: "
                   f"{intervention.final_decision} -> {outcome.value} (reward {result.reward:+.2f})")
        written = await tools.invoke("update_memory", {
            "memory_type": MemoryType.EPISODIC.value, "content": content, "importance": round(0.4 + 0.3 * abs(result.reward), 3),
            "meta": {"decision": intervention.final_decision, "outcome": outcome.value, "reward": result.reward,
                     "app_package": intervention.app_package, "hour": hour},
        }, ctx)
        result.memory_written = written.ok
        stats = await outcome_stats(ctx)
        preference = extract_preference(stats)
        source = "statistics"
        if preference is None and ctx.cloud_ai_allowed and sum(v["n"] for v in stats.values()) >= 6:
            preference, source = await _reflect(ctx, stats)
        if preference is not None:
            previous = await _current_preference(ctx)
            if previous is None or previous.meta.get("preferred") != preference["preferred"]:
                pref_text = (f"Responds better to {preference['preferred']}"
                             + (f" than {preference['avoid']}" if preference.get("avoid") else "") + f" ({preference['evidence']})")
                stored = await tools.invoke("update_memory", {
                    "memory_type": MemoryType.PREFERENCE.value, "content": pref_text, "importance": 0.8,
                    "preference_key": "intervention_preference",
                    "meta": {"preferred": preference["preferred"], "avoid": preference.get("avoid"), "source": source},
                }, ctx)
                if stored.ok:
                    result.preference = preference
                    result.insights.append(pref_text)
        result.suggestion = await _policy_suggestion(ctx, intervention, stats)
        if result.suggestion:
            result.insights.append(result.suggestion["message"])
    return result


async def _current_preference(ctx: RunContext) -> Memory | None:
    from app.memory.service import MemoryService

    return await MemoryService(ctx.session, ctx.deps.embedder).current_preference(ctx.user_id, "intervention_preference", ctx.now)


async def _reflect(ctx: RunContext, stats: dict[str, dict[str, float]]) -> tuple[dict[str, Any] | None, str]:
    request = LLMRequest(purpose="learning_reflection", system=system_prompt("learning"), max_tokens=300,
                         messages=(ChatMessage("user", json.dumps({"task": "reflect_on_outcomes", "stats": stats})),))
    result = await ctx.gateway.generate(request, LearningReflectionOutput, user_id=ctx.user_id, run_id=ctx.run_id)
    if not result.ok or result.parsed is None:
        return None, "none"
    reflection = result.parsed
    if reflection.confidence < 0.5 or not validate_reflection(reflection, stats):
        ctx.notes.append("LLM reflection discarded: not supported by outcome statistics")
        return None, "none"
    pref = reflection.preferred_intervention
    avoid = reflection.avoid_intervention
    pref = require(pref, "preferred intervention missing after reflection")
    return ({"preferred": pref.value, "avoid": avoid.value if avoid else None,
             "evidence": "; ".join(reflection.observations)[:200] or "LLM reflection validated against statistics"},
            "llm_reflection_validated")


async def _policy_suggestion(ctx: RunContext, intervention: Intervention, stats: dict[str, dict[str, float]]) -> dict[str, Any] | None:
    rule_id = (intervention.command or {}).get("rule_id")
    if not rule_id or InterventionType(intervention.final_decision) not in HARD:
        return None
    rows = (
        await ctx.session.execute(
            select(Intervention.command, InterventionOutcome.outcome)
            .join(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
            .where(Intervention.user_id == ctx.user_id, Intervention.created_at >= ctx.now - timedelta(days=30))
        )
    ).all()
    overrides = sum(1 for command, o in rows if (command or {}).get("rule_id") == rule_id and o in NEGATIVE)
    if overrides < 3:
        return None
    existing = (await ctx.session.execute(select(Memory).where(Memory.user_id == ctx.user_id,
                                                              Memory.memory_type == MemoryType.INSIGHT.value))).scalars()
    if any((m.meta or {}).get("rule_id") == rule_id and (m.meta or {}).get("status") == "open" for m in existing):
        return None
    message = (f"You overrode restrictions from rule {rule_id} {overrides} times this month. "
               "Suggestion: switch it to adaptive escalation so MindGuard can use delays or warnings when they work better.")
    stored = await ctx.deps.tools.invoke("update_memory", {
        "memory_type": MemoryType.INSIGHT.value, "content": message, "importance": 0.7,
        "meta": {"kind": "policy_suggestion", "rule_id": rule_id, "change": "escalation=adaptive", "status": "open",
                 "overrides": overrides},
    }, ctx)
    return {"rule_id": rule_id, "change": {"escalation": "adaptive"}, "message": message, "stored": stored.ok}
