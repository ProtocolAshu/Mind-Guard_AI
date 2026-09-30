"""Policy / Guardrail Engine node helpers: policy evaluation and authorization with tickets."""

from __future__ import annotations

import uuid

from app.agents.runtime import RunContext
from app.observability import metrics
from app.policies.guardrails import GuardrailContext, GuardrailLimits
from app.schemas.agents import (
    AuthorizationResult,
    ContentAssessment,
    ContextSnapshot,
    DecisionProposal,
    OverrideSnapshot,
)
from app.schemas.common import ContentCategory, InterventionStyle, InterventionType
from app.schemas.policy import PolicyContext, PolicyRule, PolicyVerdict
from app.security.tickets import issue_ticket
from app.services.interventions import recent_interventions
from app.services.users import device_capabilities

TICKET_TTL_SECONDS = 120


def policy_context(context: ContextSnapshot, content: ContentAssessment | None) -> PolicyContext:
    return PolicyContext(
        weekday=context.weekday,
        minute_of_day=context.minute_of_day,
        focus_session_active=context.focus_session_active,
        app_package=context.app_package,
        app_category=context.app_category,
        content_category=content.category if content else ContentCategory.UNKNOWN,
        content_confidence=content.confidence if content else 0.0,
        session_minutes=context.session_minutes,
        daily_minutes_by_app=context.daily_minutes_by_app,
        daily_minutes_by_category=context.daily_minutes_by_category,
        warned_this_session=context.warned_this_session,
    )


def find_rule(rules: list[PolicyRule], rule_id: str | None) -> PolicyRule | None:
    return next((r for r in rules if r.rule_id == rule_id), None) if rule_id else None


async def authorize(ctx: RunContext, *, proposal: DecisionProposal, app_package: str | None, risk: float,
                    verdict: PolicyVerdict, overrides: list[OverrideSnapshot]) -> AuthorizationResult:
    settings = ctx.deps.settings
    gctx = GuardrailContext(
        now=ctx.now,
        app_package=app_package,
        risk=risk,
        verdict=verdict,
        style=InterventionStyle(ctx.prefs.intervention_style),
        guardian_enabled=bool(ctx.prefs.guardian_enabled),
        consents=ctx.consents,
        overrides=overrides,
        capabilities=device_capabilities(ctx.prefs),
        recent=await recent_interventions(ctx.session, ctx.user_id, now=ctx.now),
        limits=GuardrailLimits(min_confidence_hard=settings.min_confidence_hard_action),
    )
    result = ctx.deps.guardrails.authorize(proposal, gctx)
    for flag in result.flags:
        metrics.GUARDRAIL_FLAGS.labels(flag.value).inc()
    if result.decision is not InterventionType.ALLOW and result.suppressed_duplicate_of is None:
        claims = {"run_id": str(ctx.run_id), "user_id": str(ctx.user_id), "app_package": app_package or "",
                  "decision": result.decision.value, "duration_minutes": result.duration_minutes, "nonce": uuid.uuid4().hex}
        result = result.model_copy(update={"ticket": issue_ticket(ctx.deps.ticket_key, claims, now=ctx.now,
                                                                  ttl_seconds=TICKET_TTL_SECONDS)})
    return result
