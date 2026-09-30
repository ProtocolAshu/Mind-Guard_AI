"""Decision Agent (section 4.7): deterministic ladder -> contextual bandit inside the safe
set -> optional LLM reasoning (with a bounded read-only tool loop) for ambiguous cases.

Memory priority (section 45): policy bounds the eligible set; a recent preference memory
outranks evidence from similar older episodes, and neither can leave the eligible set.
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select

from app.agents.prompts import system_prompt
from app.agents.runtime import RunContext
from app.bandit.policies import BanditContext, context_vector, make_bandit
from app.database.models import BanditState, InterventionOutcome
from app.decision.ladder import LadderInput, recommend
from app.memory.service import MemoryService
from app.providers.base import ChatMessage, LLMRequest
from app.schemas.agents import (
    BehaviorProfile,
    ContentAssessment,
    ContextSnapshot,
    DecisionProposal,
    DecisionRecord,
    RiskAssessment,
)
from app.schemas.common import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    ConsentScope,
    ContentCategory,
    DecisionSource,
    InterventionStyle,
    InterventionType,
    MemoryType,
    ReasonCode,
)
from app.schemas.llm import DecisionTurn, FinalDecisionTurn, ToolRequestTurn
from app.schemas.policy import Escalation, PolicyRule, PolicyVerdict
from app.security.injection import sanitize_model_output

MAX_TOOL_TURNS = 2
SIMILAR_MIN_SIMILARITY = 0.35


def _time_bucket(minute_of_day: int) -> str:
    h = minute_of_day // 60
    return "late night" if h >= 22 or h < 5 else "morning" if h < 12 else "afternoon" if h < 17 else "evening"


async def preferred_intervention(ctx: RunContext, context: ContextSnapshot, risk: RiskAssessment,
                                 eligible: tuple[InterventionType, ...]) -> tuple[InterventionType | None, str | None]:
    if not ctx.has(ConsentScope.MEMORY_PERSONALIZATION):
        return None, None
    memories = MemoryService(ctx.session, ctx.deps.embedder)
    pref = await memories.current_preference(ctx.user_id, "intervention_preference", ctx.now)
    pref_choice = None
    if pref is not None and (pref.meta or {}).get("preferred") in {e.value for e in eligible}:
        pref_choice = InterventionType(pref.meta["preferred"])
        if (ctx.now - pref.created_at).days <= 14:
            return pref_choice, "recent preference memory"
    query = f"{context.app_name or context.app_package} {_time_bucket(context.minute_of_day)} session risk {risk.band}"
    similar = await memories.retrieve(ctx.user_id, query, now=ctx.now, k=6, types=[MemoryType.EPISODIC])
    stats: dict[str, list[float]] = defaultdict(list)
    for hit in similar:
        decision, reward = hit.meta.get("decision"), hit.meta.get("reward")
        if hit.similarity >= SIMILAR_MIN_SIMILARITY and decision in {e.value for e in eligible} and isinstance(reward, int | float):
            stats[decision].append(float(reward))
    good = [(d, sum(r) / len(r)) for d, r in stats.items() if len(r) >= 2 and sum(r) / len(r) >= 0.5]
    if good:
        best = max(good, key=lambda x: x[1])[0]
        return InterventionType(best), "similar past episodes"
    return pref_choice, "older preference memory" if pref_choice else None


async def _load_bandit(ctx: RunContext):  # type: ignore[no-untyped-def]
    row = await ctx.session.get(BanditState, ctx.user_id)
    return make_bandit(ctx.deps.settings.bandit_algorithm, row.state if row else None)


async def _previous_reward(ctx: RunContext) -> float:
    value = (await ctx.session.execute(select(InterventionOutcome.reward).where(InterventionOutcome.user_id == ctx.user_id)
                                       .order_by(InterventionOutcome.created_at.desc()).limit(1))).scalar_one_or_none()
    return float(value) if value is not None else 0.0


async def decide(ctx: RunContext, *, context: ContextSnapshot, content: ContentAssessment | None, behavior: BehaviorProfile,
                 risk: RiskAssessment, verdict: PolicyVerdict, winning_rule: PolicyRule | None) -> DecisionRecord:
    notes: list[str] = []
    style = InterventionStyle(ctx.prefs.intervention_style)
    has_goal = bool(context.active_goals)
    ladder_probe = recommend(LadderInput(risk=risk, verdict=verdict, winning_rule=winning_rule, style=style,
                                         warned_this_session=context.warned_this_session,
                                         focus_active=context.focus_session_active, has_goal=has_goal,
                                         content_category=content.category if content else ContentCategory.UNKNOWN,
                                         content_confidence=content.confidence if content else 0.0))
    preferred, why = (None, None)
    if len(ladder_probe.eligible) > 1:
        preferred, why = await preferred_intervention(ctx, context, risk, ladder_probe.eligible)
        if why:
            notes.append(f"memory: {why}")
    ladder = recommend(LadderInput(risk=risk, verdict=verdict, winning_rule=winning_rule, style=style,
                                   warned_this_session=context.warned_this_session, focus_active=context.focus_session_active,
                                   has_goal=has_goal, content_category=content.category if content else ContentCategory.UNKNOWN,
                                   content_confidence=content.confidence if content else 0.0, preferred=preferred))
    notes.append(f"ladder stage {ladder.stage}: {ladder.rationale}")
    choice, source = ladder.recommended, DecisionSource.DETERMINISTIC
    codes = list(ladder.reason_codes)
    scores: dict[str, float] | None = None
    vector: list[float] | None = None
    if len(ladder.eligible) > 1:
        bandit = await _load_bandit(ctx)
        vector = context_vector(BanditContext(
            risk=risk.distraction_risk, goal_conflict=risk.goal_conflict, hour=context.minute_of_day // 60,
            focus_mode=context.focus_session_active, session_minutes=context.session_minutes,
            app_category=context.app_category, has_goal=has_goal,
            content_category=(content.category if content else ContentCategory.UNKNOWN).value,
            prev_result=await _previous_reward(ctx), override_rate=behavior.override_rate, style=style))
        selection = bandit.select(vector, list(ladder.eligible), ladder.recommended)
        scores = selection.scores or None
        notes.append(f"bandit[{bandit.algorithm}]: {selection.reason}")
        if selection.arm != ladder.recommended:
            choice, source = selection.arm, DecisionSource.BANDIT
            codes.append(ReasonCode.PERSONALIZED_CHOICE)
        elif bandit.total_updates >= bandit.warmup:
            source = DecisionSource.BANDIT
    cap = MAX_DURATION_MINUTES[choice]
    duration = min(DEFAULT_DURATION_MINUTES.get(choice, 0), verdict.max_duration_minutes or cap, cap)
    proposal = DecisionProposal(decision=choice, duration_minutes=duration, confidence=ladder.confidence,
                                reason_codes=list(dict.fromkeys(codes))[:8])
    llm_used = False

    settings = ctx.deps.settings
    ambiguous = (
        len(ladder.eligible) > 1
        and verdict.escalation is not Escalation.DIRECT
        and settings.llm_ambiguity_low <= risk.distraction_risk <= settings.llm_ambiguity_high
    )
    if ambiguous and ctx.cloud_ai_allowed:
        proposal, llm_used, source = await _llm_refine(ctx, proposal, source, ladder.eligible, context, risk, verdict,
                                                        winning_rule, behavior, notes)
    elif ambiguous:
        notes.append("ambiguous case, but cloud AI reasoning is not enabled: deterministic decision")
    return DecisionRecord(proposal=proposal, source=source, eligible=list(ladder.eligible), prior=ladder.recommended,
                          bandit_scores=scores, bandit_context=vector, llm_used=llm_used, notes=notes)


async def _llm_refine(ctx: RunContext, proposal: DecisionProposal, source: DecisionSource,
                      eligible: tuple[InterventionType, ...], context: ContextSnapshot, risk: RiskAssessment,
                      verdict: PolicyVerdict, rule: PolicyRule | None, behavior: BehaviorProfile,
                      notes: list[str]) -> tuple[DecisionProposal, bool, DecisionSource]:
    tools = ctx.deps.tools.names(llm_callable=True)
    observations: list[dict[str, object]] = []
    for _turn in range(MAX_TOOL_TURNS + 1):
        payload = {
            "task": "choose_intervention",
            "app_name": context.app_name,
            "time_of_day": _time_bucket(context.minute_of_day),
            "session_minutes": round(context.session_minutes),
            "focus_session_active": context.focus_session_active,
            "risk": {"distraction": risk.distraction_risk, "doomscroll": risk.doomscroll_probability,
                     "goal_conflict": risk.goal_conflict, "confidence": risk.confidence,
                     "top_factors": [f.description for f in risk.factors[:3]]},
            "policy": {"effect": verdict.effect.value if verdict.effect else None,
                       "rule": rule.description if rule else None},
            "eligible": [e.value for e in eligible],
            "recommended": proposal.decision.value,
            "recommended_duration": proposal.duration_minutes,
            "effectiveness": behavior.effectiveness_by_intervention,
            "reason_codes": [c.value for c in proposal.reason_codes],
            "available_tools": tools,
            "observations": observations,
        }
        request = LLMRequest(purpose="decision", system=system_prompt("decision"), tier="reasoning", max_tokens=400,
                             messages=(ChatMessage("user", json.dumps(payload, default=str)),))
        result = await ctx.gateway.generate(request, DecisionTurn, user_id=ctx.user_id, run_id=ctx.run_id,
                                            use_cache=not observations)
        if not result.ok or result.parsed is None:
            notes.append(f"LLM {result.status.value}: {result.error or 'no output'}; deterministic fallback")
            return _with_code(proposal, ReasonCode.LLM_FALLBACK), False, source
        turn = result.parsed.root
        if isinstance(turn, ToolRequestTurn):
            if len(observations) >= MAX_TOOL_TURNS:
                notes.append("LLM exceeded tool budget; deterministic fallback")
                return _with_code(proposal, ReasonCode.LLM_FALLBACK), False, source
            tool_result = await ctx.deps.tools.invoke(turn.tool, turn.arguments, ctx, caller="llm")
            summary: object = None
            if tool_result.ok and tool_result.output is not None:
                data = tool_result.output.model_dump(mode="json")
                if "memories" in data:
                    summary = [{"content": m["content"], "similarity": m["similarity"], "recent": m["is_recent"]}
                               for m in data["memories"]]
                else:
                    summary = {k: data[k] for k in list(data)[:12]}
            observations.append({"tool": turn.tool, "status": tool_result.status.value, "result": summary,
                                 "error": tool_result.error})
            notes.append(f"LLM requested tool {turn.tool} -> {tool_result.status.value}")
            continue
        if not isinstance(turn, FinalDecisionTurn):  # defensive: the gateway validates the schema first
            continue
        if turn.decision not in eligible:
            notes.append(f"LLM proposed {turn.decision.value} outside the eligible set; rejected")
            return _with_code(proposal, ReasonCode.LLM_FALLBACK), False, source
        cap = MAX_DURATION_MINUTES[turn.decision]
        duration = min(turn.duration_minutes or DEFAULT_DURATION_MINUTES.get(turn.decision, 0), cap)
        codes = list(dict.fromkeys([*proposal.reason_codes, *turn.reason_codes]))[:8]
        refined = DecisionProposal(decision=turn.decision, duration_minutes=duration,
                                   confidence=round(min(turn.confidence, proposal.confidence + 0.1), 4), reason_codes=codes,
                                   user_visible_explanation=sanitize_model_output(turn.user_visible_explanation, 280))
        notes.append(f"LLM chose {turn.decision.value} (model {result.model})")
        return refined, True, DecisionSource.LLM
    return _with_code(proposal, ReasonCode.LLM_FALLBACK), False, source


def _with_code(proposal: DecisionProposal, code: ReasonCode) -> DecisionProposal:
    codes = list(dict.fromkeys([*proposal.reason_codes, code]))[:8]
    return proposal.model_copy(update={"reason_codes": codes})
