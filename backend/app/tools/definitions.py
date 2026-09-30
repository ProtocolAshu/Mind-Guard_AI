"""The 13 agent tools of section 23."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.agents.content_agent import analyze_text
from app.agents.policy_agent import find_rule, policy_context
from app.agents.risk_agent import risk_input_from
from app.agents.runtime import RunContext
from app.bandit.policies import N_FEATURES, make_bandit
from app.database.models import BanditState, Event, Intervention, UsageSession
from app.memory.service import MemoryService
from app.observability import metrics
from app.schemas.agents import (
    ActionResult,
    BehaviorProfile,
    ContentAssessment,
    ContextSnapshot,
    GoalSnapshot,
    MemoryHit,
    RiskAssessment,
)
from app.schemas.common import (
    ConsentScope,
    ContentSource,
    DecisionSource,
    EventType,
    InterventionStatus,
    InterventionType,
    MemorySource,
    MemoryType,
    OutcomeType,
    ReasonCode,
)
from app.schemas.policy import PACKAGE_PATTERN, PolicyRule, PolicyVerdict
from app.security.audit import append_audit
from app.security.tickets import TicketError, verify_ticket
from app.services.behavior import build_profile
from app.services.goals import active_goals
from app.services.interventions import record_outcome
from app.services.policies import active_rules
from app.services.usage import build_context, recent_usage
from app.services.users import user_timezone
from app.tools.base import StrictInput, ToolPermission, ToolRegistry, ToolSpec

USAGE = frozenset({ConsentScope.USAGE_MONITORING})
MEMORY = frozenset({ConsentScope.MEMORY_PERSONALIZATION})


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(StrictInput):
    pass


class ContextInput(StrictInput):
    app_package: str | None = Field(default=None, pattern=PACKAGE_PATTERN)
    session_minutes_hint: float | None = Field(default=None, ge=0, le=1440)


class GoalsOutput(_Out):
    goals: list[GoalSnapshot]


class PolicyOutput(_Out):
    rules: list[PolicyRule]
    rule_count: int


class UsageInput(StrictInput):
    hours: int = Field(default=24, ge=1, le=72)


class UsageItem(_Out):
    app_package: str
    app_category: str
    started_at: str
    minutes: float
    open_count: int
    focus_mode: bool
    is_open: bool


class UsageOutput(_Out):
    sessions: list[UsageItem]
    total_minutes: float


class ProfileInput(StrictInput):
    days: int = Field(default=14, ge=1, le=30)


class RiskToolInput(StrictInput):
    context: ContextSnapshot
    behavior: BehaviorProfile
    content: ContentAssessment | None = None
    verdict: PolicyVerdict


class MemoryQuery(StrictInput):
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(default=4, ge=1, le=8)
    types: list[MemoryType] | None = Field(default=None, max_length=4)


class MemoriesOutput(_Out):
    memories: list[MemoryHit]


class ClassifyInput(StrictInput):
    text: str = Field(min_length=1, max_length=40_000)
    source: ContentSource = ContentSource.TEXT
    app_package: str | None = Field(default=None, pattern=PACKAGE_PATTERN)
    allow_cloud: bool = True


class PolicyEvalInput(StrictInput):
    context: ContextSnapshot
    content: ContentAssessment | None = None


class PolicyEvalOutput(_Out):
    verdict: PolicyVerdict
    winning_rule: PolicyRule | None = None


class _ActionInput(StrictInput):
    ticket: str = Field(min_length=20, max_length=2048)
    app_package: str | None = Field(default=None, pattern=PACKAGE_PATTERN)
    session_id: uuid.UUID | None = None
    proposed: InterventionType
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=8)
    guardrail_flags: list[ReasonCode] = Field(default_factory=list, max_length=16)
    decided_by: DecisionSource
    title: str = Field(default="", max_length=80)
    explanation: str = Field(default="", max_length=400)
    explanation_points: list[str] = Field(default_factory=list, max_length=6)
    bandit_context: list[float] | None = None
    rule_id: str | None = Field(default=None, pattern=r"^r_[a-z0-9_]{2,40}$")

    @field_validator("bandit_context")
    @classmethod
    def _dims(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != N_FEATURES:
            raise ValueError(f"bandit_context must have {N_FEATURES} values")
        return v

    @field_validator("explanation_points")
    @classmethod
    def _points(cls, v: list[str]) -> list[str]:
        return [p[:200] for p in v]


class TriggerInput(_ActionInput):
    pass


class ConfirmationInput(_ActionInput):
    question: str = Field(default="", max_length=200)


class LogOutcomeInput(StrictInput):
    intervention_id: uuid.UUID
    outcome: OutcomeType
    satisfaction: int | None = Field(default=None, ge=1, le=5)


class LogOutcomeOutput(_Out):
    intervention_id: uuid.UUID
    outcome: OutcomeType
    reward: float
    decision: InterventionType
    bandit_updated: bool


class UpdateMemoryInput(StrictInput):
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=1000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    meta: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=20)
    preference_key: str | None = Field(default=None, pattern=r"^[a-z_]{3,40}$")


class MemoryWriteOutput(_Out):
    memory_id: uuid.UUID
    superseded_previous: bool


async def _get_current_context(ctx: RunContext, args: ContextInput) -> ContextSnapshot:
    goals = await active_goals(ctx.session, ctx.user_id, ctx.now)
    return await build_context(ctx.session, ctx.user_id, now=ctx.now, tz=user_timezone(ctx.prefs), app_package=args.app_package,
                               goals=goals, daily_limit_minutes=ctx.prefs.daily_social_limit_minutes,
                               session_minutes_hint=args.session_minutes_hint)


async def _get_user_goals(ctx: RunContext, _: NoInput) -> GoalsOutput:
    return GoalsOutput(goals=await active_goals(ctx.session, ctx.user_id, ctx.now))


async def _get_user_policy(ctx: RunContext, _: NoInput) -> PolicyOutput:
    rules = await active_rules(ctx.session, ctx.user_id, ctx.now)
    return PolicyOutput(rules=rules, rule_count=len(rules))


async def _get_recent_usage(ctx: RunContext, args: UsageInput) -> UsageOutput:
    items = [UsageItem(**i) for i in await recent_usage(ctx.session, ctx.user_id, now=ctx.now, hours=args.hours)]
    return UsageOutput(sessions=items, total_minutes=round(sum(i.minutes for i in items), 1))


async def _get_behavior_profile(ctx: RunContext, args: ProfileInput) -> BehaviorProfile:
    return await build_profile(ctx.session, ctx.user_id, now=ctx.now, days=args.days)


async def _calculate_risk(ctx: RunContext, args: RiskToolInput) -> RiskAssessment:
    return ctx.deps.risk_scorer.score(risk_input_from(args.context, args.behavior, args.content, args.verdict))


async def _retrieve_memories(ctx: RunContext, args: MemoryQuery) -> MemoriesOutput:
    hits = await MemoryService(ctx.session, ctx.deps.embedder).retrieve(ctx.user_id, args.query, now=ctx.now, k=args.k,
                                                                         types=args.types)
    return MemoriesOutput(memories=hits)


async def _classify_content(ctx: RunContext, args: ClassifyInput) -> ContentAssessment:
    goals = await active_goals(ctx.session, ctx.user_id, ctx.now)
    return await analyze_text(ctx, text=args.text, source=args.source, goals=goals, app_package=args.app_package,
                              allow_cloud=args.allow_cloud)


async def _evaluate_policy(ctx: RunContext, args: PolicyEvalInput) -> PolicyEvalOutput:
    rules = await active_rules(ctx.session, ctx.user_id, ctx.now)
    verdict = ctx.deps.policy_engine.evaluate(rules, policy_context(args.context, args.content))
    return PolicyEvalOutput(verdict=verdict, winning_rule=find_rule(rules, verdict.winning_rule_id))


async def _create_intervention(ctx: RunContext, args: _ActionInput, allowed: set[InterventionType]) -> ActionResult:
    claims = verify_ticket(ctx.deps.ticket_key, args.ticket, now=ctx.now,
                           expected={"user_id": ctx.user_id, "run_id": ctx.run_id, "app_package": args.app_package or ""})
    decision = InterventionType(claims["decision"])
    duration = int(claims["duration_minutes"])
    if decision not in allowed:
        raise TicketError(f"ticket authorizes {decision.value}, not an action of this tool")
    nonce = str(claims["nonce"])
    replay = (await ctx.session.execute(select(Intervention.id).where(
        Intervention.user_id == ctx.user_id, Intervention.command["ticket_nonce"].as_string() == nonce))).first()
    if replay:
        raise TicketError("ticket already used")
    session_id = None
    if args.session_id:
        owned = (await ctx.session.execute(select(UsageSession.id).where(UsageSession.id == args.session_id,
                                                                         UsageSession.user_id == ctx.user_id))).first()
        session_id = args.session_id if owned else None
    row = Intervention(user_id=ctx.user_id, session_id=session_id, run_id=ctx.run_id, app_package=args.app_package,
                       proposed_decision=args.proposed.value, final_decision=decision.value, duration_minutes=duration,
                       confidence=args.confidence, reason_codes=[c.value for c in args.reason_codes],
                       guardrail_flags=[c.value for c in args.guardrail_flags], explanation=args.explanation,
                       decided_by=args.decided_by.value, status=InterventionStatus.DELIVERED.value,
                       bandit_context={"vector": args.bandit_context, "algorithm": ctx.deps.settings.bandit_algorithm}
                       if args.bandit_context else None,
                       expires_at=ctx.deps.interventions.expiry(decision, duration, ctx.now), created_at=ctx.now)
    ctx.session.add(row)
    await ctx.session.flush()
    body = " ".join(args.explanation_points) if args.explanation_points else args.explanation
    if isinstance(args, ConfirmationInput) and args.question:
        body = f"{args.question} {body}".strip()
    command = ctx.deps.interventions.build(decision, duration, intervention_id=row.id, app_package=args.app_package,
                            title=args.title or args.explanation, body=body, now=ctx.now)
    row.command = {**command.model_dump(mode="json"), "ticket_nonce": nonce, "rule_id": args.rule_id}
    ctx.session.add(Event(user_id=ctx.user_id, session_id=session_id, client_event_id=f"srv-{uuid.uuid4().hex[:24]}",
                          event_type=EventType.INTERVENTION_TRIGGERED.value, app_package=args.app_package,
                          occurred_at=ctx.now, received_at=ctx.now,
                          payload={"intervention_id": str(row.id), "decision": decision.value, "duration_minutes": duration}))
    await append_audit(ctx.session, actor=f"agent:{args.decided_by.value}", action="intervention.triggered",
                       resource_type="intervention", resource_id=row.id,
                       details={"decision": decision.value, "proposed": args.proposed.value, "duration_minutes": duration,
                                "flags": [c.value for c in args.guardrail_flags], "run_id": str(ctx.run_id)},
                       user_id=ctx.user_id, now=ctx.now)
    metrics.INTERVENTIONS.labels(decision.value, args.decided_by.value).inc()
    return ActionResult(intervention_id=row.id, command=command, explanation=args.explanation,
                        explanation_points=args.explanation_points, created=True)


async def _trigger_allowed_intervention(ctx: RunContext, args: TriggerInput) -> ActionResult:
    allowed: set[InterventionType] = {t for t in InterventionType if t is not InterventionType.ALLOW}
    return await _create_intervention(ctx, args, allowed)


async def _request_user_confirmation(ctx: RunContext, args: ConfirmationInput) -> ActionResult:
    return await _create_intervention(ctx, args, {InterventionType.REQUEST_CONFIRMATION})


async def _log_outcome(ctx: RunContext, args: LogOutcomeInput) -> LogOutcomeOutput:
    intervention, outcome = await record_outcome(ctx.session, ctx.user_id, args.intervention_id, args.outcome,
                                                 satisfaction=args.satisfaction, now=ctx.now)
    updated = False
    vector = (intervention.bandit_context or {}).get("vector")
    if isinstance(vector, list) and len(vector) == N_FEATURES:
        state = (await ctx.session.execute(select(BanditState).where(BanditState.user_id == ctx.user_id).with_for_update())
                 ).scalar_one_or_none()
        bandit = make_bandit(ctx.deps.settings.bandit_algorithm, state.state if state else None)
        bandit.update(InterventionType(intervention.final_decision), vector, outcome.reward)
        if state is None:
            state = BanditState(user_id=ctx.user_id, algorithm=bandit.algorithm, n_features=N_FEATURES)
            ctx.session.add(state)
        state.algorithm, state.state, state.total_updates, state.updated_at = (bandit.algorithm, bandit.to_state(),
                                                                               bandit.total_updates, ctx.now)
        updated = True
    event = EventType.INTERVENTION_OVERRIDDEN if args.outcome in (OutcomeType.OVERRIDDEN, OutcomeType.DISABLED_PROTECTION) \
        else EventType.INTERVENTION_ACCEPTED if outcome.reward > 0 else None
    if event is not None:
        ctx.session.add(Event(user_id=ctx.user_id, session_id=intervention.session_id,
                              client_event_id=f"srv-{uuid.uuid4().hex[:24]}", event_type=event.value,
                              app_package=intervention.app_package, occurred_at=ctx.now, received_at=ctx.now,
                              payload={"intervention_id": str(intervention.id), "outcome": args.outcome.value}))
    await ctx.session.flush()
    return LogOutcomeOutput(intervention_id=intervention.id, outcome=args.outcome, reward=outcome.reward,
                            decision=InterventionType(intervention.final_decision), bandit_updated=updated)


async def _update_memory(ctx: RunContext, args: UpdateMemoryInput) -> MemoryWriteOutput:
    service = MemoryService(ctx.session, ctx.deps.embedder)
    meta: dict[str, Any] = dict(args.meta)
    if args.preference_key:
        if args.memory_type is not MemoryType.PREFERENCE:
            from app.core.errors import ValidationFailedError

            raise ValidationFailedError("preference_key requires memory_type=preference")
        previous = await service.current_preference(ctx.user_id, args.preference_key, ctx.now)
        memory = await service.upsert_preference(ctx.user_id, args.preference_key, args.content, now=ctx.now,
                                                 importance=args.importance, meta=meta)
        return MemoryWriteOutput(memory_id=memory.id, superseded_previous=previous is not None)
    memory = await service.add(ctx.user_id, args.memory_type, args.content, now=ctx.now, importance=args.importance,
                               source=MemorySource.LEARNING, meta=meta)
    return MemoryWriteOutput(memory_id=memory.id, superseded_previous=False)


def build_tool_registry() -> ToolRegistry:
    r = ToolRegistry()
    specs = [
        ToolSpec("get_current_context", "Assemble the current context snapshot for an app.", ContextInput, ContextSnapshot,
                 ToolPermission.READ, _get_current_context, USAGE),
        ToolSpec("get_user_goals", "List the user's active goals.", NoInput, GoalsOutput, ToolPermission.READ, _get_user_goals),
        ToolSpec("get_user_policy", "Load the user's active compiled policy rules.", NoInput, PolicyOutput,
                 ToolPermission.READ, _get_user_policy),
        ToolSpec("get_recent_usage", "Summarise app sessions from the last N hours.", UsageInput, UsageOutput,
                 ToolPermission.READ, _get_recent_usage, USAGE, llm_callable=True),
        ToolSpec("get_behavior_profile", "Aggregate behaviour profile over recent days.", ProfileInput, BehaviorProfile,
                 ToolPermission.READ, _get_behavior_profile, USAGE, llm_callable=True),
        ToolSpec("calculate_risk", "Score distraction/doomscroll risk from structured state.", RiskToolInput, RiskAssessment,
                 ToolPermission.COMPUTE, _calculate_risk),
        ToolSpec("retrieve_memories", "Vector search over the user's memories.", MemoryQuery, MemoriesOutput,
                 ToolPermission.READ, _retrieve_memories, MEMORY, llm_callable=True),
        ToolSpec("classify_content", "Classify sanitised third-party text (local first, LLM if ambiguous).", ClassifyInput,
                 ContentAssessment, ToolPermission.COMPUTE, _classify_content, timeout_seconds=20.0),
        ToolSpec("evaluate_policy", "Evaluate the user's rules against context and content.", PolicyEvalInput,
                 PolicyEvalOutput, ToolPermission.READ, _evaluate_policy),
        ToolSpec("request_user_confirmation", "Ask the user to confirm (requires an authorization ticket).",
                 ConfirmationInput, ActionResult, ToolPermission.DEVICE_ACTION, _request_user_confirmation),
        ToolSpec("trigger_allowed_intervention", "Create a guardrail-authorized device command (requires a ticket).",
                 TriggerInput, ActionResult, ToolPermission.DEVICE_ACTION, _trigger_allowed_intervention),
        ToolSpec("log_outcome", "Record an intervention outcome, compute reward and update the bandit.", LogOutcomeInput,
                 LogOutcomeOutput, ToolPermission.WRITE, _log_outcome),
        ToolSpec("update_memory", "Store a sanitised memory (never raw content).", UpdateMemoryInput, MemoryWriteOutput,
                 ToolPermission.WRITE, _update_memory, MEMORY),
    ]
    for spec in specs:
        r.register(spec)
    return r
