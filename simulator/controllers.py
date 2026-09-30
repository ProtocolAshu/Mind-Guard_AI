"""Controllers compared in the experiments (section 27) and the ablations (section 55).

Baselines 1-4 are deliberately simple and faithful to common products. The proposed
controller is *the real MindGuard decision stack* (constitution compiler, policy engine,
risk scorer, decision ladder, contextual bandit, preference memory, guardrails) run
in-process without the database, so the experiment evaluates the shipped logic.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.agents.learning_agent import extract_preference
from app.bandit.policies import BanditContext, LinUCB, context_vector
from app.decision.ladder import LadderInput, recommend
from app.policies.compiler import ConstitutionCompiler
from app.policies.engine import EFFECT_INTERVENTION, evaluate_policy
from app.policies.guardrails import GuardrailContext, GuardrailLimits, Guardrails
from app.risk.features import RiskInput
from app.risk.scoring import RiskScorer
from app.schemas.agents import DecisionProposal, RecentIntervention
from app.schemas.common import (
    DEFAULT_DURATION_MINUTES,
    AppCategory,
    ContentCategory,
    InterventionStatus,
    InterventionStyle,
    InterventionType as IT,
    OutcomeType,
)
from app.schemas.policy import PolicyContext
from simulator.personas import Persona
from simulator.world import DayPlan, PlannedSession

BASE_DATE = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday


@dataclass
class Observation:
    day: int
    weekday: int
    minute_of_day: int
    session: PlannedSession
    session_minutes: float
    in_focus: bool
    daily_social_minutes: float
    daily_minutes_by_app: dict[str, float]
    daily_minutes_by_category: dict[str, float]
    opens_last_hour: int
    warned_this_session: bool
    plan: DayPlan

    @property
    def now(self) -> datetime:
        return BASE_DATE + timedelta(days=self.day, minutes=self.minute_of_day)


class Controller(Protocol):
    name: str

    def decide(self, obs: Observation) -> tuple[IT, int] | None: ...
    def observe(self, obs: Observation, intervention: IT, duration: int, outcome: OutcomeType, reward: float) -> None: ...
    def end_day(self, stats: dict[str, float]) -> None: ...


class _Base:
    name = "base"

    def observe(self, obs: Observation, intervention: IT, duration: int, outcome: OutcomeType, reward: float) -> None:
        return None

    def end_day(self, stats: dict[str, float]) -> None:
        return None


class NoIntervention(_Base):
    name = "B1_no_intervention"

    def decide(self, obs: Observation) -> tuple[IT, int] | None:
        return None


class StaticLimit(_Base):
    """Daily social-media limit; once exceeded, block (re-prompting every 10 minutes)."""

    name = "B2_static_limit"

    def __init__(self, limit_minutes: float = 60.0):
        self.limit = limit_minutes
        self.last_prompt: dict[tuple[int, int], float] = {}

    def decide(self, obs: Observation) -> tuple[IT, int] | None:
        if obs.daily_social_minutes < self.limit:
            return None
        key = (obs.day, obs.session.index)
        if obs.session_minutes - self.last_prompt.get(key, -99) < 10:
            return None
        self.last_prompt[key] = obs.session_minutes
        return IT.TEMPORARY_BLOCK, 15


class ReminderOnly(_Base):
    name = "B3_reminder_only"

    def decide(self, obs: Observation) -> tuple[IT, int] | None:
        return (IT.SOFT_WARNING, 0) if obs.session_minutes >= 15 and int(obs.session_minutes) % 15 == 0 else None


class RuleBased(_Base):
    """The user's compiled rules applied directly: no risk model, escalation, learning or guardrails."""

    name = "B4_rule_based"

    def __init__(self, persona: Persona):
        self.rules = ConstitutionCompiler().compile(persona.constitution).constitution.rules
        self.last_prompt: dict[tuple[int, int], float] = {}

    def decide(self, obs: Observation) -> tuple[IT, int] | None:
        verdict = evaluate_policy(self.rules, _policy_context(obs))
        if verdict.effect is None or EFFECT_INTERVENTION[verdict.effect] is IT.ALLOW:
            return None
        key = (obs.day, obs.session.index)
        if obs.session_minutes - self.last_prompt.get(key, -99) < 10:
            return None
        self.last_prompt[key] = obs.session_minutes
        intervention = EFFECT_INTERVENTION[verdict.effect]
        return intervention, DEFAULT_DURATION_MINUTES.get(intervention, 0)


def _policy_context(obs: Observation) -> PolicyContext:
    return PolicyContext(weekday=obs.weekday, minute_of_day=obs.minute_of_day, focus_session_active=obs.in_focus,
                         app_package=obs.session.app_package, app_category=AppCategory(obs.session.app_category),
                         content_category=obs.session.observed_content, content_confidence=obs.session.observed_confidence,
                         session_minutes=obs.session_minutes, daily_minutes_by_app=obs.daily_minutes_by_app,
                         daily_minutes_by_category=obs.daily_minutes_by_category, warned_this_session=obs.warned_this_session)


@dataclass
class _History:
    days: deque[dict[str, float]] = field(default_factory=lambda: deque(maxlen=14))
    hourly: deque[list[float]] = field(default_factory=lambda: deque(maxlen=14))
    outcomes: deque[tuple[IT, OutcomeType, float]] = field(default_factory=lambda: deque(maxlen=400))


class MindGuard(_Base):
    def __init__(self, persona: Persona, *, name: str = "F_full_system", memory: bool = True, context: bool = True,
                 personalization: bool = True, bandit: bool = True, rule_only: bool = False, scorer: RiskScorer | None = None):
        self.name = name
        self.persona = persona
        self.rules = ConstitutionCompiler().compile(persona.constitution).constitution.rules
        self.style = InterventionStyle(persona.style)
        self.memory = memory and not rule_only
        self.context = context
        self.personalization = personalization and not rule_only
        self.rule_only = rule_only
        self.bandit = LinUCB() if bandit and not rule_only else None
        self.scorer = scorer or RiskScorer()
        self.guardrails = Guardrails(GuardrailLimits())
        self.history = _History()
        self.recent: list[RecentIntervention] = []
        self.pending_vector: dict[uuid.UUID, list[float]] = {}
        self.last_reward = 0.0
        self.today_hourly = [0.0] * 24
        self._preference: str | None = None

    # -- personalization features ------------------------------------------------
    def _profile(self) -> dict[str, float | tuple[int, ...]]:
        if not self.personalization or not self.history.days:
            return {"trigger_hours": (), "avg_session": 0.0, "long_rate": 0.0, "override_rate": 0.0, "accept_rate": 0.0, "days": 0}
        sessions = sum(d["sessions"] for d in self.history.days)
        long_sessions = sum(d["long_sessions"] for d in self.history.days)
        minutes = sum(d["social_minutes"] for d in self.history.days)
        hourly = [sum(h[i] for h in self.history.hourly) for i in range(24)]
        total = sum(hourly) or 1.0
        triggers = tuple(sorted(h for h in sorted(range(24), key=lambda h: -hourly[h])[:3] if hourly[h] >= 1.5 * total / 24))
        outcomes = list(self.history.outcomes)
        overrides = sum(1 for _, o, _ in outcomes if o is OutcomeType.OVERRIDDEN)
        accepts = sum(1 for _, o, _ in outcomes if o is OutcomeType.ACCEPTED)
        return {"trigger_hours": triggers, "avg_session": minutes / sessions if sessions else 0.0,
                "long_rate": long_sessions / sessions if sessions else 0.0,
                "override_rate": overrides / len(outcomes) if outcomes else 0.0,
                "accept_rate": accepts / len(outcomes) if outcomes else 0.0, "days": len(self.history.days)}

    def _preferred(self, eligible: tuple[IT, ...]) -> IT | None:
        if not self.memory:
            return None
        pref = self._preference
        return IT(pref) if pref and IT(pref) in eligible else None

    def decide(self, obs: Observation) -> tuple[IT, int] | None:
        now = obs.now
        hour = obs.minute_of_day // 60
        verdict = evaluate_policy(self.rules, _policy_context(obs))
        rule = next((r for r in self.rules if r.rule_id == verdict.winning_rule_id), None)
        profile = self._profile()
        since = [r for r in self.recent if r.created_at >= now - timedelta(hours=24)]
        last = max((r.created_at for r in since), default=None)
        risk = self.scorer.score(RiskInput(
            session_minutes=obs.session_minutes, hour=hour if self.context else 12, minute=obs.minute_of_day % 60 if self.context else 0,
            weekday=obs.weekday if self.context else 2, focus_active=obs.in_focus and self.context,
            in_restricted_window=self.context and verdict.in_restricted_window and verdict.effect is not None and verdict.effect.value != "ALLOW",
            content_category=obs.session.observed_content, content_confidence=obs.session.observed_confidence,
            app_category=AppCategory(obs.session.app_category), opens_last_hour=obs.opens_last_hour if self.context else 0,
            daily_social_minutes=obs.daily_social_minutes, trigger_hours=profile["trigger_hours"],  # type: ignore[arg-type]
            avg_session_minutes_14d=float(profile["avg_session"]), long_session_rate_14d=float(profile["long_rate"]),  # type: ignore[arg-type]
            override_rate_14d=float(profile["override_rate"]), accept_rate_14d=float(profile["accept_rate"]),  # type: ignore[arg-type]
            minutes_since_last_intervention=(now - last).total_seconds() / 60 if last else None, history_days=int(profile["days"]),  # type: ignore[arg-type]
        ), use_ml=not self.rule_only)
        probe = recommend(LadderInput(risk=risk, verdict=verdict, winning_rule=rule, style=self.style,
                                      warned_this_session=obs.warned_this_session, focus_active=obs.in_focus,
                                      has_goal=True, content_category=obs.session.observed_content,
                                      content_confidence=obs.session.observed_confidence))
        ladder = recommend(LadderInput(risk=risk, verdict=verdict, winning_rule=rule, style=self.style,
                                       warned_this_session=obs.warned_this_session, focus_active=obs.in_focus, has_goal=True,
                                       content_category=obs.session.observed_content,
                                       content_confidence=obs.session.observed_confidence,
                                       preferred=self._preferred(probe.eligible)))
        choice, vector = ladder.recommended, None
        if self.bandit is not None and len(ladder.eligible) > 1:
            vector = context_vector(BanditContext(
                risk=risk.distraction_risk, goal_conflict=risk.goal_conflict, hour=hour if self.context else 12,
                focus_mode=obs.in_focus and self.context, session_minutes=obs.session_minutes,
                app_category=AppCategory(obs.session.app_category), has_goal=True,
                content_category=obs.session.observed_content.value, prev_result=self.last_reward,
                override_rate=float(profile["override_rate"]), style=self.style))  # type: ignore[arg-type]
            choice = self.bandit.select(vector, list(ladder.eligible), ladder.recommended).arm
        if choice is IT.ALLOW:
            return None
        duration = min(DEFAULT_DURATION_MINUTES.get(choice, 0), verdict.max_duration_minutes or 999)
        proposal = DecisionProposal(decision=choice, duration_minutes=duration, confidence=ladder.confidence)
        auth = self.guardrails.authorize(proposal, GuardrailContext(
            now=now, app_package=obs.session.app_package, risk=risk.distraction_risk, verdict=verdict, style=self.style,
            recent=since))
        if auth.decision is IT.ALLOW or auth.suppressed_duplicate_of is not None:
            return None
        record = RecentIntervention(id=uuid.uuid4(), decision=auth.decision, app_package=obs.session.app_package,
                                    status=InterventionStatus.DELIVERED, created_at=now,
                                    expires_at=now + timedelta(minutes=auth.duration_minutes or 10))
        self.recent.append(record)
        if vector is not None:
            self.pending_vector[record.id] = vector
        self._last_record = record
        return auth.decision, auth.duration_minutes

    def observe(self, obs: Observation, intervention: IT, duration: int, outcome: OutcomeType, reward: float) -> None:
        record = self._last_record
        status = {OutcomeType.ACCEPTED: InterventionStatus.ACCEPTED, OutcomeType.OVERRIDDEN: InterventionStatus.OVERRIDDEN}.get(
            outcome, InterventionStatus.IGNORED)
        self.recent[-1] = record.model_copy(update={"status": status})
        vector = self.pending_vector.pop(record.id, None)
        if self.bandit is not None and vector is not None:
            self.bandit.update(intervention, vector, reward)
        self.last_reward = reward
        self.history.outcomes.append((intervention, outcome, reward))
        if self.memory:
            stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "reward_sum": 0.0, "overrides": 0})
            for decision, o, r in list(self.history.outcomes)[-120:]:
                s = stats[decision.value]
                s["n"] += 1
                s["reward_sum"] += r
                s["overrides"] += o is OutcomeType.OVERRIDDEN
            summary = {d: {"n": v["n"], "mean_reward": v["reward_sum"] / v["n"], "overrides": v["overrides"]} for d, v in stats.items()}
            pref = extract_preference(summary)
            self._preference = pref["preferred"] if pref else self._preference

    def end_day(self, stats: dict[str, float]) -> None:
        self.history.days.append(stats)
        self.history.hourly.append(list(stats.get("hourly", [0.0] * 24)))  # type: ignore[arg-type]
        self.recent = [r for r in self.recent if r.created_at >= BASE_DATE + timedelta(days=stats["day"] - 1)]


def controller_factories(persona: Persona, scorer: RiskScorer | None = None) -> dict[str, object]:
    return {
        "B1_no_intervention": lambda: NoIntervention(),
        "B2_static_limit": lambda: StaticLimit(),
        "B3_reminder_only": lambda: ReminderOnly(),
        "B4_rule_based": lambda: RuleBased(persona),
        "A_no_memory": lambda: MindGuard(persona, name="A_no_memory", memory=False, scorer=scorer),
        "B_no_context": lambda: MindGuard(persona, name="B_no_context", context=False, scorer=scorer),
        "C_no_personalization": lambda: MindGuard(persona, name="C_no_personalization", personalization=False, memory=False, scorer=scorer),
        "D_no_bandit": lambda: MindGuard(persona, name="D_no_bandit", bandit=False, scorer=scorer),
        "E_rule_only": lambda: MindGuard(persona, name="E_rule_only", rule_only=True, scorer=scorer),
        "F_full_system": lambda: MindGuard(persona, name="F_full_system", scorer=scorer),
    }


__all__ = ["ContentCategory", "Controller", "MindGuard", "NoIntervention", "Observation", "ReminderOnly", "RuleBased",
           "StaticLimit", "controller_factories"]
