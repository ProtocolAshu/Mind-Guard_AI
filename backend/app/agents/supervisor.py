"""Supervisor / Orchestrator (section 4.1): explicit LangGraph state machines.

Evaluation graph
    START -> supervisor --(emergency / paused / disabled / essential app / no consent)--> guardrail
                        \\-> context -> goal -> content -> behavior -> policy -> risk -> decision -> guardrail
    guardrail -> action -> END

Every node is instrumented (agent_steps row + OpenTelemetry span + Prometheus histogram)
and has a deterministic fallback, so one failing component degrades the run instead of
breaking it (section 24). The guardrail node fails *open*: without authorization nothing
is restricted, which preserves user autonomy and access to essential functions.
"""

from __future__ import annotations

import logging
import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.action_agent import build_command, compose_explanation
from app.agents.content_agent import metadata_assessment
from app.agents.decision_agent import decide
from app.agents.goal_agent import compile_constitution
from app.agents.learning_agent import LearningError, learn
from app.agents.policy_agent import authorize, find_rule
from app.agents.recorder import RunRecorder, summarize
from app.agents.risk_agent import persist_risk
from app.agents.runtime import AgentDeps, RunContext
from app.core.errors import require
from app.database.models import ContentSignal
from app.decision.ladder import LadderInput, recommend
from app.observability.tracing import get_tracer
from app.policies import catalog
from app.policies.guardrails import PAUSE_KINDS
from app.risk.scoring import score_rules
from app.schemas.agents import (
    ActionResult,
    AuthorizationResult,
    BehaviorProfile,
    ContentAssessment,
    ContextSnapshot,
    DecisionProposal,
    DecisionRecord,
    DeviceCommand,
    OverrideSnapshot,
    RiskAssessment,
)
from app.schemas.common import (
    AppCategory,
    ConsentScope,
    ContentSource,
    DecisionSource,
    InterventionStyle,
    InterventionType,
    OverrideKind,
    ReasonCode,
    RiskMethod,
    RunStatus,
    RunType,
)
from app.schemas.evaluation import (
    ConstitutionPreviewResponse,
    ConstitutionRequest,
    EvaluateRequest,
    EvaluationResponse,
    FeedbackRequest,
    FeedbackResponse,
    PolicySummary,
    RiskSummary,
)
from app.schemas.policy import PolicyRule, PolicyVerdict
from app.services.interventions import get_intervention
from app.services.policies import active_overrides, consume_override
from app.services.users import get_preferences, granted_scopes, user_timezone

log = logging.getLogger(__name__)


class EvalState(TypedDict, total=False):
    run_id: str
    user_id: str
    request: dict[str, Any]
    route: str
    plan: list[str]
    short_circuit: dict[str, Any] | None
    goal: dict[str, Any]
    context: dict[str, Any]
    content: dict[str, Any]
    behavior: dict[str, Any]
    policy: dict[str, Any]
    risk: dict[str, Any]
    decision: dict[str, Any]
    authorization: dict[str, Any]
    action: dict[str, Any]
    outcome: dict[str, Any]
    path: Annotated[list[str], operator.add]
    errors: Annotated[list[dict[str, str]], operator.add]


class ToolFailure(RuntimeError):
    pass


@dataclass
class Runtime:
    ctx: RunContext
    request: Any
    overrides: list[OverrideSnapshot] = field(default_factory=list)
    rules: list[PolicyRule] = field(default_factory=list)
    seq: int = 0
    result: Any = None


NodeFn = Callable[[EvalState, Runtime], Awaitable[dict[str, Any]]]
Fallback = Callable[[EvalState, Runtime], dict[str, Any]]
INPUT_KEYS: dict[str, tuple[str, ...]] = {
    "supervisor": ("request",), "context": ("request",), "goal": (), "content": ("request",), "behavior": (),
    "policy": ("context", "content"), "risk": ("policy",), "decision": ("risk", "policy"),
    "guardrail": ("decision", "short_circuit"), "action": ("authorization",), "learning": ("request",),
}


def instrumented(name: str, fallback: Fallback) -> Callable[[NodeFn], Callable[..., Awaitable[dict[str, Any]]]]:
    def decorate(fn: NodeFn) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def node(state: EvalState, config: RunnableConfig) -> dict[str, Any]:
            rt: Runtime = config["configurable"]["runtime"]
            rt.seq += 1
            seq, started_at, t0 = rt.seq, datetime.now(UTC), time.perf_counter()
            status, error = "ok", None
            with get_tracer().start_as_current_span(f"agent.{name}") as span:
                span.set_attribute("mindguard.run_id", str(rt.ctx.run_id))
                try:
                    async with rt.ctx.session.begin_nested():
                        update = await fn(state, rt)
                except Exception as exc:
                    log.exception("agent node %s failed; using fallback", name)
                    status, error = "fallback", f"{type(exc).__name__}: {str(exc)[:240]}"
                    span.record_exception(exc)
                    update = fallback(state, rt)
                    update["errors"] = [{"node": name, "error": error}]
                span.set_attribute("mindguard.status", status)
            latency = (time.perf_counter() - t0) * 1000
            if rt.ctx.recorder is not None and rt.ctx.run_id is not None:
                await rt.ctx.recorder.step(
                    run_id=rt.ctx.run_id, seq=seq, node=name, status=status, latency_ms=latency, started_at=started_at,
                    input_summary={k: summarize(state.get(k)) for k in INPUT_KEYS.get(name, ()) if k in state},
                    output_summary={k: v for k, v in update.items() if k not in ("path", "errors")}, error=error)
            update["path"] = [name]
            return update

        node.__name__ = f"{name}_node"
        return node

    return decorate


async def _invoke(rt: Runtime, tool: str, args: dict[str, Any]) -> Any:
    result = await rt.ctx.deps.tools.invoke(tool, args, rt.ctx)
    if not result.ok:
        raise ToolFailure(f"{tool}: {result.status.value} ({result.error})")
    return result.output


# ----------------------------------------------------------------------------- evaluation nodes

@instrumented("supervisor", lambda s, rt: {"route": "short_circuit", "short_circuit": {"reason": None}})
async def supervisor_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    ctx, req = rt.ctx, rt.request
    rt.overrides = await active_overrides(ctx.session, ctx.user_id, ctx.now)
    kinds = {o.kind for o in rt.overrides}
    reason: ReasonCode | None = None
    if OverrideKind.EMERGENCY in kinds:
        reason = ReasonCode.EMERGENCY_OVERRIDE
    elif not ctx.prefs.guardian_enabled:
        reason = ReasonCode.GUARDIAN_DISABLED
    elif kinds & PAUSE_KINDS:
        reason = ReasonCode.GUARDIAN_PAUSED
    elif catalog.is_essential(req.app_package):
        reason = ReasonCode.ESSENTIAL_APP_PROTECTED
    elif ConsentScope.USAGE_MONITORING not in ctx.consents:
        reason = ReasonCode.CONSENT_MISSING
    if reason is not None:
        return {"route": "short_circuit", "plan": ["guardrail", "action"], "short_circuit": {"reason": reason.value}}
    return {"route": "full", "short_circuit": None,
            "plan": ["context", "goal", "content", "behavior", "policy", "risk", "decision", "guardrail", "action"]}


def _context_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    tz = user_timezone(rt.ctx.prefs)
    local = rt.ctx.now.astimezone(tz)
    app = catalog.lookup(rt.request.app_package)
    snapshot = ContextSnapshot(now_utc=rt.ctx.now, local_time=local.strftime("%H:%M"), timezone=str(tz), weekday=local.weekday(),
                               minute_of_day=local.hour * 60 + local.minute, is_night=local.hour >= 22 or local.hour < 5,
                               is_weekend=local.weekday() >= 5, app_package=rt.request.app_package,
                               app_name=app.name if app else rt.request.app_package,
                               app_category=app.category if app else AppCategory.OTHER,
                               session_minutes=rt.request.session_minutes or 0.0)
    return {"context": snapshot.model_dump(mode="json")}


@instrumented("context", _context_fallback)
async def context_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    caps = rt.request.device_capabilities
    if caps is not None:
        merged = dict(rt.ctx.prefs.device_capabilities or {})
        merged.update({k: v for k, v in caps.model_dump().items() if v is not None})
        rt.ctx.prefs.device_capabilities = merged
    snapshot = await _invoke(rt, "get_current_context", {"app_package": rt.request.app_package,
                                                         "session_minutes_hint": rt.request.session_minutes})
    return {"context": snapshot.model_dump(mode="json")}


@instrumented("goal", lambda s, rt: {"goal": {"goals": [], "rule_ids": []}})
async def goal_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    goals = await _invoke(rt, "get_user_goals", {})
    policy = await _invoke(rt, "get_user_policy", {})
    rt.rules = list(policy.rules)
    return {"goal": {"goals": [g.model_dump(mode="json") for g in goals.goals], "rule_ids": [r.rule_id for r in rt.rules]}}


def _content_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    return {"content": metadata_assessment(rt.request.app_package, []).model_dump(mode="json")}


@instrumented("content", _content_fallback)
async def content_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    context = ContextSnapshot.model_validate(state["context"])
    req = rt.request
    if req.content is None:
        assessment = metadata_assessment(req.app_package, context.active_goals)
    else:
        assessment = await _invoke(rt, "classify_content", {"text": req.content.text, "source": req.content.source.value,
                                                           "app_package": req.app_package})
        if assessment.source is not ContentSource.METADATA:
            rt.ctx.session.add(ContentSignal(
                user_id=rt.ctx.user_id, session_id=context.session_id, source=assessment.source.value,
                category=assessment.category.value, goal_relevance=assessment.goal_relevance.value,
                confidence=assessment.confidence, classifier=assessment.classifier,
                injection_detected=assessment.injection_detected, injection_score=assessment.injection_score,
                text_sha256=assessment.text_sha256, created_at=rt.ctx.now))
    return {"content": assessment.model_dump(mode="json")}


@instrumented("behavior", lambda s, rt: {"behavior": BehaviorProfile().model_dump(mode="json")})
async def behavior_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    profile = await _invoke(rt, "get_behavior_profile", {"days": 14})
    return {"behavior": profile.model_dump(mode="json")}


@instrumented("policy", lambda s, rt: {"policy": {"verdict": PolicyVerdict().model_dump(mode="json"), "winning_rule": None}})
async def policy_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    out = await _invoke(rt, "evaluate_policy", {"context": state["context"], "content": state.get("content")})
    return {"policy": {"verdict": out.verdict.model_dump(mode="json"),
                       "winning_rule": out.winning_rule.model_dump(mode="json") if out.winning_rule else None}}


def _risk_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    from app.agents.risk_agent import risk_input_from

    try:
        context = ContextSnapshot.model_validate(state["context"])
        inp = risk_input_from(context, BehaviorProfile(), None, PolicyVerdict.model_validate(state["policy"]["verdict"]))
        rules = score_rules(inp)
        risk = RiskAssessment(distraction_risk=rules.distraction, doomscroll_probability=rules.doomscroll,
                              goal_conflict=rules.goal_conflict, intervention_urgency=rules.distraction,
                              continuation_probability=rules.continuation, predicted_productivity_loss_minutes=0.0,
                              confidence=min(rules.confidence, 0.5), method=RiskMethod.RULES, factors=rules.factors)
    except Exception:
        risk = RiskAssessment(distraction_risk=0.0, doomscroll_probability=0.0, goal_conflict=0.0, intervention_urgency=0.0,
                              continuation_probability=0.0, predicted_productivity_loss_minutes=0.0, confidence=0.3,
                              method=RiskMethod.RULES)
    return {"risk": risk.model_dump(mode="json")}


@instrumented("risk", _risk_fallback)
async def risk_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    risk = await _invoke(rt, "calculate_risk", {"context": state["context"], "behavior": state["behavior"],
                                               "content": state.get("content"), "verdict": state["policy"]["verdict"]})
    context = ContextSnapshot.model_validate(state["context"])
    await persist_risk(rt.ctx.session, user_id=rt.ctx.user_id, run_id=rt.ctx.run_id, session_id=context.session_id, risk=risk,
                       now=rt.ctx.now)
    return {"risk": risk.model_dump(mode="json")}


def _decision_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    try:
        context = ContextSnapshot.model_validate(state["context"])
        risk = RiskAssessment.model_validate(state["risk"])
        verdict = PolicyVerdict.model_validate(state["policy"]["verdict"])
        content = ContentAssessment.model_validate(state["content"]) if state.get("content") else ContentAssessment()
        ladder = recommend(LadderInput(risk=risk, verdict=verdict, winning_rule=find_rule(rt.rules, verdict.winning_rule_id),
                                       style=InterventionStyle(rt.ctx.prefs.intervention_style),
                                       warned_this_session=context.warned_this_session,
                                       focus_active=context.focus_session_active, has_goal=bool(context.active_goals),
                                       content_category=content.category, content_confidence=content.confidence))
        proposal = DecisionProposal(decision=ladder.recommended, duration_minutes=ladder.duration_minutes,
                                    confidence=ladder.confidence,
                                    reason_codes=list(dict.fromkeys([*ladder.reason_codes, ReasonCode.LLM_FALLBACK]))[:8])
        record = DecisionRecord(proposal=proposal, source=DecisionSource.FALLBACK, eligible=list(ladder.eligible),
                                prior=ladder.recommended, notes=["decision agent failed; deterministic ladder used"])
    except Exception:
        record = DecisionRecord(proposal=DecisionProposal(decision=InterventionType.ALLOW, confidence=0.3,
                                                          reason_codes=[ReasonCode.LOW_CONFIDENCE]),
                                source=DecisionSource.FALLBACK, eligible=[InterventionType.ALLOW], prior=InterventionType.ALLOW)
    return {"decision": record.model_dump(mode="json")}


@instrumented("decision", _decision_fallback)
async def decision_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    verdict = PolicyVerdict.model_validate(state["policy"]["verdict"])
    content = ContentAssessment.model_validate(state["content"]) if state.get("content") else None
    record = await decide(rt.ctx, context=ContextSnapshot.model_validate(state["context"]), content=content,
                          behavior=BehaviorProfile.model_validate(state["behavior"]),
                          risk=RiskAssessment.model_validate(state["risk"]), verdict=verdict,
                          winning_rule=find_rule(rt.rules, verdict.winning_rule_id))
    if content is not None and content.injection_detected:
        codes = list(dict.fromkeys([*record.proposal.reason_codes, ReasonCode.UNTRUSTED_CONTENT_QUARANTINED]))[:8]
        record = record.model_copy(update={"proposal": record.proposal.model_copy(update={"reason_codes": codes})})
    return {"decision": record.model_dump(mode="json")}


def _guardrail_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    proposed = InterventionType((state.get("decision") or {}).get("proposal", {}).get("decision", "ALLOW"))
    result = AuthorizationResult(proposed=proposed, decision=InterventionType.ALLOW, duration_minutes=0,
                                 authorized_unchanged=proposed is InterventionType.ALLOW, flags=[ReasonCode.LOW_CONFIDENCE],
                                 notes=["Authorization is unavailable, so nothing was restricted."])
    return {"authorization": result.model_dump(mode="json")}


@instrumented("guardrail", _guardrail_fallback)
async def guardrail_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    if state.get("decision"):
        proposal = DecisionRecord.model_validate(state["decision"]).proposal
    else:
        reason = (state.get("short_circuit") or {}).get("reason")
        proposal = DecisionProposal(decision=InterventionType.ALLOW, confidence=1.0,
                                    reason_codes=[ReasonCode(reason)] if reason else [ReasonCode.LOW_CONFIDENCE])
    verdict = PolicyVerdict.model_validate(state["policy"]["verdict"]) if state.get("policy") else PolicyVerdict()
    risk = float((state.get("risk") or {}).get("distraction_risk", 0.0))
    result = await authorize(rt.ctx, proposal=proposal, app_package=rt.request.app_package, risk=risk, verdict=verdict,
                             overrides=rt.overrides)
    return {"authorization": result.model_dump(mode="json")}


def _action_fallback(state: EvalState, rt: Runtime) -> dict[str, Any]:
    command = build_command(InterventionType.ALLOW, 0, intervention_id=None, app_package=rt.request.app_package,
                            title="No action", body="", now=rt.ctx.now)
    return {"action": ActionResult(command=command, explanation="The intervention could not be delivered; nothing was restricted.",
                                   created=False).model_dump(mode="json")}


@instrumented("action", _action_fallback)
async def action_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    ctx, req = rt.ctx, rt.request
    auth = AuthorizationResult.model_validate(state["authorization"])
    context = ContextSnapshot.model_validate(state["context"]) if state.get("context") else None
    risk = RiskAssessment.model_validate(state["risk"]) if state.get("risk") else None
    record = DecisionRecord.model_validate(state["decision"]) if state.get("decision") else None
    verdict = PolicyVerdict.model_validate(state["policy"]["verdict"]) if state.get("policy") else PolicyVerdict()
    rule = find_rule(rt.rules, verdict.winning_rule_id)
    if auth.consumed_override_id is not None:
        await consume_override(ctx.session, ctx.user_id, auth.consumed_override_id, ctx.now)
    if auth.suppressed_duplicate_of is not None:
        existing = await get_intervention(ctx.session, ctx.user_id, auth.suppressed_duplicate_of)
        command = DeviceCommand.model_validate({k: v for k, v in (existing.command or {}).items() if k in DeviceCommand.model_fields})
        return {"action": ActionResult(intervention_id=existing.id, command=command, explanation=existing.explanation,
                                       explanation_points=["An equal or stronger intervention is already active."],
                                       created=False).model_dump(mode="json")}
    codes = list(record.proposal.reason_codes) if record else []
    headline, points = compose_explanation(
        decision=auth.decision, duration=auth.duration_minutes, context=context, risk=risk, rule=rule, reason_codes=codes,
        authorization=auth, llm_sentence=record.proposal.user_visible_explanation if record and record.source is DecisionSource.LLM else "")
    if auth.decision is InterventionType.ALLOW:
        command = build_command(InterventionType.ALLOW, 0, intervention_id=None, app_package=req.app_package, title=headline,
                                body=" ".join(points), now=ctx.now)
        return {"action": ActionResult(command=command, explanation=headline, explanation_points=points).model_dump(mode="json")}
    tool = "request_user_confirmation" if auth.decision is InterventionType.REQUEST_CONFIRMATION else "trigger_allowed_intervention"
    args: dict[str, Any] = {
        "ticket": auth.ticket, "app_package": req.app_package, "session_id": str(context.session_id) if context and context.session_id else None,
        "proposed": auth.proposed.value, "confidence": record.proposal.confidence if record else 1.0,
        "reason_codes": [c.value for c in codes[:8]], "guardrail_flags": [f.value for f in auth.flags[:16]],
        "decided_by": (record.source if record else DecisionSource.OVERRIDE).value, "title": headline[:80],
        "explanation": headline, "explanation_points": points, "bandit_context": record.bandit_context if record else None,
        "rule_id": verdict.winning_rule_id,
    }
    if tool == "request_user_confirmation":
        args["question"] = "Is this session intentional?"
    result = await _invoke(rt, tool, args)
    return {"action": result.model_dump(mode="json")}


def build_evaluation_graph() -> Any:
    graph = StateGraph(EvalState)
    nodes = [("supervisor", supervisor_node), ("context", context_node), ("goal", goal_node), ("content", content_node),
             ("behavior", behavior_node), ("policy", policy_node), ("risk", risk_node), ("decision", decision_node),
             ("guardrail", guardrail_node), ("action", action_node)]
    for name, fn in nodes:
        graph.add_node(name, fn)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", lambda s: "guardrail" if s.get("route") == "short_circuit" else "context",
                                {"guardrail": "guardrail", "context": "context"})
    for a, b in [("context", "goal"), ("goal", "content"), ("content", "behavior"), ("behavior", "policy"), ("policy", "risk"),
                 ("risk", "decision"), ("decision", "guardrail"), ("guardrail", "action"), ("action", END)]:
        graph.add_edge(a, b)
    return graph.compile()


# ----------------------------------------------------------------------------- feedback & constitution graphs

@instrumented("learning", lambda s, rt: {"outcome": {"error": "learning failed"}})
async def learning_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    req: FeedbackRequest = rt.request
    rt.result = await learn(rt.ctx, intervention_id=str(req.intervention_id), outcome=req.outcome, satisfaction=req.satisfaction)
    return {"outcome": {"reward": rt.result.reward, "bandit_updated": rt.result.bandit_updated,
                        "preference": rt.result.preference, "suggestion": bool(rt.result.suggestion)}}


def _learning_graph() -> Any:
    graph = StateGraph(EvalState)
    graph.add_node("learning", learning_node)
    graph.add_edge(START, "learning")
    graph.add_edge("learning", END)
    return graph.compile()


@instrumented("goal", lambda s, rt: {"goal": {"error": "compile failed"}})
async def constitution_node(state: EvalState, rt: Runtime) -> dict[str, Any]:
    req: ConstitutionRequest = rt.request
    rt.result = await compile_constitution(rt.ctx, req.text, allow_llm=req.allow_llm)
    return {"goal": {"rules": len(rt.result.constitution.rules), "unsupported": len(rt.result.constitution.unsupported),
                     "compiled_by": rt.result.compiled_by.value, "valid": rt.result.validation.valid}}


def _constitution_graph() -> Any:
    graph = StateGraph(EvalState)
    graph.add_node("goal", constitution_node)
    graph.add_edge(START, "goal")
    graph.add_edge("goal", END)
    return graph.compile()


class Orchestrator:
    def __init__(self, deps: AgentDeps, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self.deps = deps
        self.session_factory = session_factory
        self.evaluation_graph = build_evaluation_graph()
        self.feedback_graph = _learning_graph()
        self.constitution_graph = _constitution_graph()

    async def context_for(self, session: AsyncSession, user_id: uuid.UUID, now: datetime) -> RunContext:
        prefs = await get_preferences(session, user_id)
        consents = await granted_scopes(session, user_id)
        recorder = RunRecorder(session, self.session_factory)
        return RunContext(session=session, deps=self.deps, user_id=user_id, now=now, prefs=prefs, consents=consents,
                          gateway=self.deps.gateway_for(session, recorder, now), recorder=recorder)

    async def _run(self, graph: Any, ctx: RunContext, run_type: RunType, request: Any) -> tuple[dict[str, Any], Runtime, float]:
        recorder = require(ctx.recorder, "recorder missing")
        t0 = time.perf_counter()
        ctx.run_id = await recorder.start_run(user_id=ctx.user_id, run_type=run_type,
                                              trigger=request.model_dump(mode="json"), now=ctx.now)
        rt = Runtime(ctx=ctx, request=request)
        state: EvalState = {"run_id": str(ctx.run_id), "user_id": str(ctx.user_id), "request": request.model_dump(mode="json"),
                            "path": [], "errors": []}
        try:
            final = await graph.ainvoke(state, config={"configurable": {"runtime": rt}, "recursion_limit": 40})
        except Exception as exc:
            await recorder.finish_run(require(ctx.run_id, "run id missing"), run_type=run_type, status=RunStatus.FAILED, path=[],
                                      final_decision=None, error=f"{type(exc).__name__}: {exc}",
                                      latency_ms=(time.perf_counter() - t0) * 1000, now=ctx.now)
            raise
        return final, rt, (time.perf_counter() - t0) * 1000

    async def evaluate(self, session: AsyncSession, user_id: uuid.UUID, request: EvaluateRequest, now: datetime,
                       run_type: RunType = RunType.EVALUATE) -> EvaluationResponse:
        ctx = await self.context_for(session, user_id, now)
        final, rt, latency = await self._run(self.evaluation_graph, ctx, run_type, request)
        auth = AuthorizationResult.model_validate(final["authorization"])
        action = ActionResult.model_validate(final["action"])
        record = DecisionRecord.model_validate(final["decision"]) if final.get("decision") else None
        risk = RiskAssessment.model_validate(final["risk"]) if final.get("risk") else None
        verdict = PolicyVerdict.model_validate(final["policy"]["verdict"]) if final.get("policy") else None
        rule = find_rule(rt.rules, verdict.winning_rule_id) if verdict else None
        codes = record.proposal.reason_codes if record else []
        degraded = bool(final.get("errors")) or ReasonCode.LLM_FALLBACK in codes
        recorder, run_id = require(ctx.recorder, "recorder missing"), require(ctx.run_id, "run id missing")
        await recorder.finish_run(run_id, run_type=run_type,
                                  status=RunStatus.DEGRADED if degraded else RunStatus.SUCCEEDED, path=final.get("path", []),
                                  final_decision=auth.decision.value,
                                  error="; ".join(e["error"] for e in final.get("errors", [])) or None,
                                  latency_ms=latency, now=now)
        return EvaluationResponse(
            run_id=ctx.run_id, decision=auth.decision, proposed=auth.proposed, duration_minutes=auth.duration_minutes,
            confidence=record.proposal.confidence if record else 1.0,
            decided_by=record.source if record else DecisionSource.OVERRIDE, reason_codes=list(codes),
            guardrail_flags=auth.flags, explanation=action.explanation, explanation_points=action.explanation_points,
            command=action.command, intervention_id=action.intervention_id, risk=RiskSummary.of(risk) if risk else None,
            policy=PolicySummary(effect=verdict.effect, winning_rule_id=verdict.winning_rule_id,
                                 rule_description=rule.description if rule else None, explanation=verdict.explanation,
                                 in_restricted_window=verdict.in_restricted_window) if verdict else None,
            content=ContentAssessment.model_validate(final["content"]) if final.get("content") else None,
            degraded=degraded, path=final.get("path", []),
            notes=[*(record.notes if record else []), *ctx.notes][:12],
        )

    async def feedback(self, session: AsyncSession, user_id: uuid.UUID, request: FeedbackRequest, now: datetime) -> FeedbackResponse:
        ctx = await self.context_for(session, user_id, now)
        await get_intervention(session, user_id, request.intervention_id)  # 404 for unknown / other users' ids
        final, rt, latency = await self._run(self.feedback_graph, ctx, RunType.FEEDBACK, request)
        recorder, run_id = require(ctx.recorder, "recorder missing"), require(ctx.run_id, "run id missing")
        errors = final.get("errors", [])
        await recorder.finish_run(run_id, run_type=RunType.FEEDBACK,
                                  status=RunStatus.FAILED if rt.result is None else RunStatus.SUCCEEDED,
                                  path=final.get("path", []), final_decision=None,
                                  error="; ".join(e["error"] for e in errors) or None, latency_ms=latency, now=now)
        if rt.result is None:
            message = errors[0]["error"] if errors else "learning failed"
            raise LearningError("error", message)
        r = rt.result
        return FeedbackResponse(run_id=ctx.run_id, intervention_id=uuid.UUID(r.intervention_id), outcome=r.outcome,
                                reward=r.reward, decision=r.decision, bandit_updated=r.bandit_updated,
                                memory_written=r.memory_written, preference=r.preference, suggestion=r.suggestion,
                                insights=r.insights)

    async def compile_constitution(self, session: AsyncSession, user_id: uuid.UUID, request: ConstitutionRequest,
                                   now: datetime) -> ConstitutionPreviewResponse:
        ctx = await self.context_for(session, user_id, now)
        final, rt, latency = await self._run(self.constitution_graph, ctx, RunType.CONSTITUTION, request)
        recorder, run_id = require(ctx.recorder, "recorder missing"), require(ctx.run_id, "run id missing")
        await recorder.finish_run(run_id, run_type=RunType.CONSTITUTION,
                                  status=RunStatus.SUCCEEDED if rt.result else RunStatus.FAILED, path=final.get("path", []),
                                  final_decision=None, error=None, latency_ms=latency, now=now)
        if rt.result is None:
            raise RuntimeError("constitution compilation failed")
        p = rt.result
        return ConstitutionPreviewResponse(run_id=ctx.run_id, constitution=p.constitution, compiled_by=p.compiled_by,
                                           validation=p.validation, parsed_sentences=p.parsed, unparsed_sentences=p.unparsed,
                                           summary=p.summary, llm_used=p.llm_used, grounding=p.grounding)

