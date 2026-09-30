"""Guardrail engine: the only component allowed to authorize an intervention.

AI output (bandit, LLM, deterministic ladder) is a *proposal*. `authorize` applies a
fixed, ordered pipeline of deterministic checks and returns what may actually run.
Order matters and is documented in docs/agents.md ("Guardrail pipeline"):

 1 emergency override   2 guardian paused/disabled   3 essential app   4 usage consent
 5 allow-once           6 low confidence             7 low risk        8 policy ceiling
 9 style ceiling       10 device capability         11 duration caps  12 duplicate / cooldown
13 hourly rate limit   14 daily hard-intervention budget
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.clock import ensure_utc
from app.policies import catalog
from app.policies.engine import EFFECT_INTERVENTION, EFFECT_SEVERITY
from app.schemas.agents import (
    AuthorizationResult,
    DecisionProposal,
    DeviceCapabilities,
    OverrideSnapshot,
    RecentIntervention,
)
from app.schemas.common import (
    DEFAULT_DURATION_MINUTES,
    HARD_INTERVENTIONS,
    INTERVENTION_SEVERITY,
    MAX_DURATION_MINUTES,
    STYLE_MAX_SEVERITY,
    ConsentScope,
    InterventionStatus,
    InterventionStyle,
    InterventionType,
    OverrideKind,
    PolicyEffect,
    ReasonCode,
)
from app.schemas.policy import Escalation, PolicyVerdict

PAUSE_KINDS = frozenset({OverrideKind.PAUSE, OverrideKind.DISABLE_30M, OverrideKind.DISABLE_UNTIL_TOMORROW})
ACTIVE_STATUSES = frozenset({InterventionStatus.PENDING, InterventionStatus.DELIVERED})
OVERLAY_REQUIRED = frozenset(
    {InterventionType.DELAY, InterventionType.LIMITED_ACCESS, InterventionType.TEMPORARY_BLOCK}
)
# One step down the ladder at a time when a ceiling is exceeded.
DOWNGRADE: dict[InterventionType, InterventionType] = {
    InterventionType.TEMPORARY_BLOCK: InterventionType.LIMITED_ACCESS,
    InterventionType.LIMITED_ACCESS: InterventionType.DELAY,
    InterventionType.FOCUS_MODE: InterventionType.DELAY,
    InterventionType.DELAY: InterventionType.SOFT_WARNING,
    InterventionType.REQUEST_CONFIRMATION: InterventionType.SOFT_WARNING,
    InterventionType.MINDFUL_PROMPT: InterventionType.ALLOW,
    InterventionType.SOFT_WARNING: InterventionType.ALLOW,
    InterventionType.ALLOW: InterventionType.ALLOW,
}


@dataclass(frozen=True)
class GuardrailLimits:
    min_confidence_hard: float = 0.6
    hard_min_risk: float = 0.6
    cooldown_minutes: int = 10
    max_interventions_per_hour: int = 6
    max_hard_per_day: int = 8


@dataclass
class GuardrailContext:
    now: datetime
    app_package: str | None
    risk: float
    verdict: PolicyVerdict
    style: InterventionStyle = InterventionStyle.BALANCED
    guardian_enabled: bool = True
    consents: frozenset[ConsentScope] = frozenset({ConsentScope.USAGE_MONITORING})
    overrides: Sequence[OverrideSnapshot] = ()
    capabilities: DeviceCapabilities = field(default_factory=DeviceCapabilities)
    recent: Sequence[RecentIntervention] = ()
    limits: GuardrailLimits = field(default_factory=GuardrailLimits)


def _active(o: OverrideSnapshot, now: datetime) -> bool:
    return o.expires_at is None or ensure_utc(o.expires_at) > now


def _step_down_to(decision: InterventionType, max_severity: int) -> InterventionType:
    while INTERVENTION_SEVERITY[decision] > max_severity:
        decision = DOWNGRADE[decision]
    return decision


class Guardrails:
    def __init__(self, limits: GuardrailLimits | None = None):
        self.limits = limits or GuardrailLimits()

    def authorize(self, proposal: DecisionProposal, ctx: GuardrailContext) -> AuthorizationResult:
        now = ensure_utc(ctx.now)
        limits = ctx.limits or self.limits
        flags: list[ReasonCode] = []
        notes: list[str] = []
        proposed = proposal.decision
        decision = proposed
        duration = proposal.duration_minutes

        def result(
            final: InterventionType,
            minutes: int = 0,
            consumed: uuid.UUID | None = None,
            duplicate: uuid.UUID | None = None,
        ) -> AuthorizationResult:
            minutes = minutes if MAX_DURATION_MINUTES[final] > 0 else 0
            return AuthorizationResult(
                proposed=proposed,
                decision=final,
                duration_minutes=minutes,
                authorized_unchanged=(final == proposed and minutes == proposal.duration_minutes),
                flags=list(dict.fromkeys(flags)),
                notes=notes,
                consumed_override_id=consumed,
                suppressed_duplicate_of=duplicate,
            )

        active_overrides = [o for o in ctx.overrides if _active(o, now)]
        # 1. Emergency override always wins, with no conditions.
        if any(o.kind is OverrideKind.EMERGENCY for o in active_overrides):
            flags.append(ReasonCode.EMERGENCY_OVERRIDE)
            notes.append("Emergency override is active: every restriction is released.")
            return result(InterventionType.ALLOW)
        # 2. Guardian disabled or paused by the user.
        if not ctx.guardian_enabled:
            flags.append(ReasonCode.GUARDIAN_DISABLED)
            notes.append("Guardian is turned off.")
            return result(InterventionType.ALLOW)
        if any(o.kind in PAUSE_KINDS for o in active_overrides):
            flags.append(ReasonCode.GUARDIAN_PAUSED)
            notes.append("Guardian is paused by you.")
            return result(InterventionType.ALLOW)
        # 3. Essential apps are never restricted.
        if catalog.is_essential(ctx.app_package):
            flags.append(ReasonCode.ESSENTIAL_APP_PROTECTED)
            notes.append("Essential apps (calls, messages, maps, payments) are never restricted.")
            return result(InterventionType.ALLOW)
        # 4. No usage-monitoring consent means no basis to act.
        if ConsentScope.USAGE_MONITORING not in ctx.consents:
            flags.append(ReasonCode.CONSENT_MISSING)
            notes.append("Usage monitoring consent is not granted.")
            return result(InterventionType.ALLOW)
        if decision is InterventionType.ALLOW:
            return result(InterventionType.ALLOW)
        # 5. "Allow once" for this app.
        allow_once = next(
            (
                o
                for o in active_overrides
                if o.kind is OverrideKind.ALLOW_ONCE and (o.app_package is None or o.app_package == ctx.app_package)
            ),
            None,
        )
        if allow_once is not None:
            flags.append(ReasonCode.ALLOW_ONCE)
            notes.append("You chose 'Allow once' for this app.")
            return result(InterventionType.ALLOW, consumed=allow_once.id)

        verdict = ctx.verdict
        explicit_direct = (
            verdict.effect in (PolicyEffect.BLOCK, PolicyEffect.LIMIT) and verdict.escalation is Escalation.DIRECT
        )
        # 6. Low confidence: never hard-block, warn instead (failsafe section 24).
        if (decision in HARD_INTERVENTIONS or decision is InterventionType.DELAY) and (
            proposal.confidence < limits.min_confidence_hard
        ):
            decision = InterventionType.SOFT_WARNING
            flags.append(ReasonCode.LOW_CONFIDENCE)
            notes.append(f"Confidence {proposal.confidence:.2f} is too low for a restriction; showing a warning instead.")
        # 7. Hard interventions need real risk unless the user's own rule demands them.
        if decision in HARD_INTERVENTIONS and ctx.risk < limits.hard_min_risk and verdict.effect not in (
            PolicyEffect.BLOCK,
            PolicyEffect.LIMIT,
        ):
            decision = InterventionType.MINDFUL_PROMPT
            flags.append(ReasonCode.LOW_RISK_SESSION)
            notes.append("Risk is not high enough for a restriction without a matching rule.")
        # 8. Policy ceiling: never exceed what the matching rule allows.
        if verdict.effect is not None:
            if verdict.effect is PolicyEffect.ALLOW:
                flags.append(ReasonCode.POLICY_EXCEPTION_ALLOWS)
                notes.append(f"Your rule {verdict.winning_rule_id} allows this.")
                return result(InterventionType.ALLOW)
            ceiling = EFFECT_SEVERITY[verdict.effect]
            if INTERVENTION_SEVERITY[decision] > ceiling:
                decision = _step_down_to(decision, ceiling)
                if INTERVENTION_SEVERITY[decision] < ceiling and ceiling > 0:
                    decision = EFFECT_INTERVENTION[verdict.effect]
                flags.append(ReasonCode.POLICY_CEILING)
                notes.append(f"Capped by rule {verdict.winning_rule_id} ({verdict.effect.value}).")
        # 9. Style ceiling (an explicit DIRECT rule of the user outranks the general style preference).
        style_max = STYLE_MAX_SEVERITY[ctx.style]
        if explicit_direct and verdict.effect is not None:
            style_max = max(style_max, EFFECT_SEVERITY[verdict.effect])
        if INTERVENTION_SEVERITY[decision] > style_max:
            decision = _step_down_to(decision, style_max)
            flags.append(ReasonCode.STYLE_CEILING)
            notes.append(f"Limited to your '{ctx.style.value}' intervention style.")
        # 10. Device capability: only authorize what the device can actually deliver.
        caps = ctx.capabilities
        if decision in OVERLAY_REQUIRED and not caps.can_overlay():
            decision = InterventionType.REQUEST_CONFIRMATION if caps.can_notify() else InterventionType.ALLOW
            flags.append(ReasonCode.CAPABILITY_UNAVAILABLE)
            notes.append("Display-over-apps permission is not granted; asking via notification instead.")
        elif decision is not InterventionType.ALLOW and not (caps.can_notify() or caps.can_overlay()):
            decision = InterventionType.ALLOW
            flags.append(ReasonCode.CAPABILITY_UNAVAILABLE)
            notes.append("No notification or overlay permission: nothing can be shown.")
        if decision is InterventionType.ALLOW:
            return result(InterventionType.ALLOW)
        # 11. Duration caps.
        cap = MAX_DURATION_MINUTES[decision]
        if verdict.max_duration_minutes:
            cap = min(cap, verdict.max_duration_minutes)
        if cap > 0:
            if duration <= 0:
                duration = min(DEFAULT_DURATION_MINUTES.get(decision, cap), cap)
            elif duration > cap:
                duration = cap
                flags.append(ReasonCode.DURATION_CAPPED)
                notes.append(f"Duration capped at {cap} minutes.")
        else:
            duration = 0
        # 12. Duplicate suppression and cooldown.
        same_app = [r for r in ctx.recent if r.app_package == ctx.app_package]
        for r in same_app:
            still_active = r.status in ACTIVE_STATUSES and r.expires_at is not None and ensure_utc(r.expires_at) > now
            if still_active and INTERVENTION_SEVERITY[r.decision] >= INTERVENTION_SEVERITY[decision]:
                flags.append(ReasonCode.DUPLICATE_SUPPRESSED)
                notes.append("An equal or stronger intervention is already active for this app.")
                return result(r.decision, duration, duplicate=r.id)
        cooldown_start = now - timedelta(minutes=limits.cooldown_minutes)
        soft_repeat = [
            r
            for r in same_app
            if ensure_utc(r.created_at) >= cooldown_start
            and INTERVENTION_SEVERITY[r.decision] >= INTERVENTION_SEVERITY[decision]
        ]
        if soft_repeat and decision not in HARD_INTERVENTIONS:
            flags.append(ReasonCode.COOLDOWN)
            notes.append(f"A similar nudge was shown less than {limits.cooldown_minutes} minutes ago.")
            return result(InterventionType.ALLOW)
        # 13. Hourly rate limit (explicit DIRECT rules are exempt: the user asked for them).
        last_hour = [r for r in ctx.recent if ensure_utc(r.created_at) >= now - timedelta(hours=1)]
        if len(last_hour) >= limits.max_interventions_per_hour and not explicit_direct:
            flags.append(ReasonCode.RATE_LIMITED)
            notes.append("Intervention rate limit reached for this hour.")
            return result(InterventionType.ALLOW)
        # 14. Daily hard-intervention budget (runaway-loop failsafe).
        if decision in HARD_INTERVENTIONS and not explicit_direct:
            day_start = now - timedelta(hours=24)
            hard_today = sum(
                1 for r in ctx.recent if r.decision in HARD_INTERVENTIONS and ensure_utc(r.created_at) >= day_start
            )
            if hard_today >= limits.max_hard_per_day:
                decision = InterventionType.SOFT_WARNING
                duration = 0
                flags.append(ReasonCode.BLOCK_BUDGET_EXHAUSTED)
                notes.append("Daily restriction budget used up; warning instead.")
        return result(decision, duration)
