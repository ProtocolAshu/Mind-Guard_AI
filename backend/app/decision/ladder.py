"""Deterministic decision ladder: maps (risk band, policy verdict, escalation stage,
style, memory preference) to a recommended intervention and the *safe eligible set*
the bandit may personalise within. Also the complete fallback when LLM/bandit fail.

Memory priority (section 45) is enforced structurally here: the policy verdict
bounds the eligible set first; a remembered preference may only pick *within* it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.agents import RiskAssessment
from app.schemas.common import (
    DEFAULT_DURATION_MINUTES,
    INTERVENTION_SEVERITY,
    ContentCategory,
    InterventionStyle,
    InterventionType,
    PolicyEffect,
    ReasonCode,
)
from app.schemas.policy import Escalation, PolicyRule, PolicyVerdict

IT = InterventionType


@dataclass(frozen=True)
class LadderInput:
    risk: RiskAssessment
    verdict: PolicyVerdict
    winning_rule: PolicyRule | None
    style: InterventionStyle
    warned_this_session: bool
    focus_active: bool
    has_goal: bool
    content_category: ContentCategory
    content_confidence: float
    preferred: InterventionType | None = None


@dataclass(frozen=True)
class LadderOutput:
    recommended: InterventionType
    eligible: tuple[InterventionType, ...]
    duration_minutes: int
    confidence: float
    reason_codes: tuple[ReasonCode, ...]
    rationale: str
    stage: str


_BAND_CODE = {
    "high": ReasonCode.HIGH_RISK_SESSION,
    "elevated": ReasonCode.ELEVATED_RISK_SESSION,
    "mild": ReasonCode.MILD_RISK_SESSION,
    "low": ReasonCode.LOW_RISK_SESSION,
}


def _risk_only(inp: LadderInput) -> tuple[IT, tuple[IT, ...], str]:
    band = inp.risk.band
    focus_extra = (IT.FOCUS_MODE,) if inp.has_goal and not inp.focus_active else ()
    if band == "low":
        return IT.ALLOW, (IT.ALLOW,), "risk-low"
    if band == "mild":
        return IT.SOFT_WARNING, (IT.ALLOW, IT.SOFT_WARNING, IT.MINDFUL_PROMPT), "risk-mild"
    if band == "elevated":
        rec = IT.DELAY if inp.warned_this_session else IT.MINDFUL_PROMPT
        return rec, (IT.SOFT_WARNING, IT.MINDFUL_PROMPT, IT.DELAY, IT.REQUEST_CONFIRMATION, *focus_extra), "risk-elevated"
    hard = (IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK) if inp.focus_active else (IT.LIMITED_ACCESS,)
    rec = IT.LIMITED_ACCESS if (inp.warned_this_session and inp.focus_active) else IT.DELAY
    return rec, (IT.MINDFUL_PROMPT, IT.DELAY, IT.REQUEST_CONFIRMATION, *hard, *focus_extra), "risk-high"


def _policy(inp: LadderInput) -> tuple[IT, tuple[IT, ...], str]:
    effect = inp.verdict.effect
    escalation = inp.verdict.escalation or Escalation.SOFT_THEN_BLOCK
    band = inp.risk.band
    if effect is PolicyEffect.ALLOW:
        return IT.ALLOW, (IT.ALLOW,), "policy-exception"
    if effect is PolicyEffect.WARN:
        return IT.SOFT_WARNING, (IT.SOFT_WARNING, IT.MINDFUL_PROMPT), "policy-warn"
    if effect is PolicyEffect.DELAY:
        return IT.DELAY, (IT.DELAY,), "policy-delay"
    if effect is PolicyEffect.REQUIRE_CONFIRMATION:
        return IT.REQUEST_CONFIRMATION, (IT.REQUEST_CONFIRMATION,), "policy-confirm"
    hard = IT.TEMPORARY_BLOCK if effect is PolicyEffect.BLOCK else IT.LIMITED_ACCESS
    ceiling = (IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK) if hard is IT.TEMPORARY_BLOCK else (IT.DELAY, IT.LIMITED_ACCESS)
    if escalation is Escalation.DIRECT:
        return hard, (hard,), "policy-direct"
    if escalation is Escalation.ADAPTIVE:
        rec = hard if band == "high" else IT.DELAY
        return rec, (IT.SOFT_WARNING, IT.MINDFUL_PROMPT, *ceiling), "policy-adaptive"
    if not inp.warned_this_session:
        return IT.MINDFUL_PROMPT, (IT.SOFT_WARNING, IT.MINDFUL_PROMPT, IT.REQUEST_CONFIRMATION), "policy-nudge"
    return hard, ceiling, "policy-enforce"


def recommend(inp: LadderInput) -> LadderOutput:
    verdict = inp.verdict
    codes: list[ReasonCode] = [_BAND_CODE[inp.risk.band]]
    codes += [f.reason_code for f in inp.risk.factors if f.reason_code and f.contribution > 0][:3]
    if verdict.effect is not None:
        recommended, eligible, stage = _policy(inp)
        codes.insert(0, ReasonCode.POLICY_EXCEPTION_ALLOWS if verdict.effect is PolicyEffect.ALLOW else ReasonCode.POLICY_RULE_MATCHED)
        rationale = verdict.explanation
    elif verdict.content_dependent and verdict.in_restricted_window:
        recommended = IT.REQUEST_CONFIRMATION if inp.risk.band != "low" else IT.SOFT_WARNING
        eligible, stage = (IT.ALLOW, IT.SOFT_WARNING, IT.MINDFUL_PROMPT, IT.REQUEST_CONFIRMATION), "content-uncertain"
        codes.insert(0, ReasonCode.CONTENT_UNCERTAIN)
        rationale = verdict.explanation + " Asking instead of restricting."
    else:
        recommended, eligible, stage = _risk_only(inp)
        rationale = f"No rule applies; risk is {inp.risk.band} ({inp.risk.distraction_risk:.2f})."
    if inp.focus_active and ReasonCode.FOCUS_SESSION_ACTIVE not in codes:
        codes.append(ReasonCode.FOCUS_SESSION_ACTIVE)

    if (
        inp.preferred is not None
        and inp.preferred in eligible
        and inp.preferred != recommended
        and INTERVENTION_SEVERITY[inp.preferred] <= INTERVENTION_SEVERITY[recommended] + 1
    ):
        recommended = inp.preferred
        codes.append(ReasonCode.MEMORY_PATTERN)
        rationale += f" Your history suggests {inp.preferred.value} works better for you."

    confidence = inp.risk.confidence
    if verdict.effect is not None and verdict.effect is not PolicyEffect.ALLOW:
        confidence = max(confidence, 0.75)
        rule = inp.winning_rule
        if rule is not None and rule.condition.content_categories:
            confidence = min(confidence, inp.content_confidence + 0.15)
    elif verdict.effect is PolicyEffect.ALLOW:
        confidence = max(confidence, 0.8)
    duration = DEFAULT_DURATION_MINUTES.get(recommended, 0)
    if verdict.max_duration_minutes and duration:
        duration = min(duration, verdict.max_duration_minutes)
    return LadderOutput(
        recommended=recommended,
        eligible=tuple(dict.fromkeys(eligible)),
        duration_minutes=duration,
        confidence=round(min(0.99, max(0.0, confidence)), 4),
        reason_codes=tuple(dict.fromkeys(codes))[:8],
        rationale=rationale,
        stage=stage,
    )
