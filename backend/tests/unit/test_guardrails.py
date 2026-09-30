import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.policies.guardrails import GuardrailContext, GuardrailLimits, Guardrails
from app.schemas.agents import DecisionProposal, DeviceCapabilities, OverrideSnapshot, RecentIntervention
from app.schemas.common import (
    InterventionStatus,
    InterventionStyle,
    OverrideKind,
    PolicyEffect,
)
from app.schemas.common import (
    InterventionType as IT,
)
from app.schemas.common import (
    ReasonCode as RC,
)
from app.schemas.policy import Escalation, PolicyVerdict

NOW = datetime(2026, 9, 15, 22, 30, tzinfo=UTC)
IG = "com.instagram.android"
G = Guardrails()


def proposal(decision=IT.TEMPORARY_BLOCK, minutes=15, confidence=0.9):
    return DecisionProposal(decision=decision, duration_minutes=minutes, confidence=confidence, reason_codes=[])


def gctx(**kw):
    base = dict(now=NOW, app_package=IG, risk=0.9, verdict=PolicyVerdict(), style=InterventionStyle.STRICT)
    base.update(kw)
    return GuardrailContext(**base)


def override(kind, app=None, minutes=30):
    return OverrideSnapshot(id=uuid.uuid4(), kind=kind, app_package=app, expires_at=NOW + timedelta(minutes=minutes))


def recent(decision, minutes_ago, app=IG, status=InterventionStatus.DELIVERED, active_for=None):
    created = NOW - timedelta(minutes=minutes_ago)
    expires = created + timedelta(minutes=active_for) if active_for else None
    return RecentIntervention(id=uuid.uuid4(), decision=decision, app_package=app, status=status, created_at=created,
                              expires_at=expires)


def test_valid_high_risk_block_is_authorized_unchanged():
    r = G.authorize(proposal(), gctx())
    assert r.decision is IT.TEMPORARY_BLOCK and r.duration_minutes == 15 and r.authorized_unchanged and not r.flags


@pytest.mark.parametrize(
    ("kw", "flag"),
    [
        ({"overrides": [override(OverrideKind.EMERGENCY)]}, RC.EMERGENCY_OVERRIDE),
        ({"guardian_enabled": False}, RC.GUARDIAN_DISABLED),
        ({"overrides": [override(OverrideKind.PAUSE)]}, RC.GUARDIAN_PAUSED),
        ({"overrides": [override(OverrideKind.DISABLE_UNTIL_TOMORROW)]}, RC.GUARDIAN_PAUSED),
        ({"app_package": "com.google.android.dialer"}, RC.ESSENTIAL_APP_PROTECTED),
        ({"app_package": "com.android.emergency"}, RC.ESSENTIAL_APP_PROTECTED),
        ({"consents": frozenset()}, RC.CONSENT_MISSING),
        ({"overrides": [override(OverrideKind.ALLOW_ONCE, IG)]}, RC.ALLOW_ONCE),
    ],
)
def test_terminal_user_controls_release_everything(kw, flag):
    r = G.authorize(proposal(), gctx(**kw))
    assert r.decision is IT.ALLOW and flag in r.flags and not r.authorized_unchanged


def test_expired_override_is_ignored_and_allow_once_is_app_scoped():
    expired = OverrideSnapshot(id=uuid.uuid4(), kind=OverrideKind.PAUSE, expires_at=NOW - timedelta(minutes=1))
    assert G.authorize(proposal(), gctx(overrides=[expired])).decision is IT.TEMPORARY_BLOCK
    other_app = override(OverrideKind.ALLOW_ONCE, "com.zhiliaoapp.musically")
    assert G.authorize(proposal(), gctx(overrides=[other_app])).decision is IT.TEMPORARY_BLOCK
    once = override(OverrideKind.ALLOW_ONCE, IG)
    assert G.authorize(proposal(), gctx(overrides=[once])).consumed_override_id == once.id


def test_low_confidence_never_hard_blocks():
    r = G.authorize(proposal(confidence=0.4), gctx())
    assert r.decision is IT.SOFT_WARNING and RC.LOW_CONFIDENCE in r.flags and r.duration_minutes == 0


def test_hard_intervention_needs_risk_or_matching_rule():
    r = G.authorize(proposal(), gctx(risk=0.3))
    assert r.decision is IT.MINDFUL_PROMPT and RC.LOW_RISK_SESSION in r.flags
    rule = PolicyVerdict(effect=PolicyEffect.BLOCK, winning_rule_id="r_block_x", escalation=Escalation.DIRECT)
    assert G.authorize(proposal(), gctx(risk=0.3, verdict=rule)).decision is IT.TEMPORARY_BLOCK


def test_policy_ceiling_caps_llm_overreach_and_exception_allows():
    warn_rule = PolicyVerdict(effect=PolicyEffect.WARN, winning_rule_id="r_warn_x")
    r = G.authorize(proposal(), gctx(verdict=warn_rule))
    assert r.decision is IT.SOFT_WARNING and RC.POLICY_CEILING in r.flags
    limit_rule = PolicyVerdict(effect=PolicyEffect.LIMIT, winning_rule_id="r_limit_x")
    assert G.authorize(proposal(), gctx(verdict=limit_rule)).decision is IT.LIMITED_ACCESS
    allow_rule = PolicyVerdict(effect=PolicyEffect.ALLOW, winning_rule_id="r_allow_edu")
    r = G.authorize(proposal(), gctx(verdict=allow_rule))
    assert r.decision is IT.ALLOW and RC.POLICY_EXCEPTION_ALLOWS in r.flags


def test_style_ceiling_and_direct_rule_exemption():
    r = G.authorize(proposal(), gctx(style=InterventionStyle.GENTLE))
    assert r.decision is IT.DELAY and RC.STYLE_CEILING in r.flags and r.duration_minutes == 5
    direct = PolicyVerdict(effect=PolicyEffect.BLOCK, winning_rule_id="r_b", escalation=Escalation.DIRECT)
    assert G.authorize(proposal(), gctx(style=InterventionStyle.GENTLE, verdict=direct)).decision is IT.TEMPORARY_BLOCK


def test_device_capabilities():
    no_overlay = DeviceCapabilities(overlay=False, notifications=True)
    r = G.authorize(proposal(), gctx(capabilities=no_overlay))
    assert r.decision is IT.REQUEST_CONFIRMATION and RC.CAPABILITY_UNAVAILABLE in r.flags
    nothing = DeviceCapabilities(overlay=False, notifications=False)
    assert G.authorize(proposal(IT.SOFT_WARNING, 0), gctx(capabilities=nothing)).decision is IT.ALLOW


def test_duration_caps_and_defaults():
    r = G.authorize(proposal(minutes=240), gctx())
    assert r.duration_minutes == 60 and RC.DURATION_CAPPED in r.flags
    rule = PolicyVerdict(effect=PolicyEffect.BLOCK, winning_rule_id="r_b", max_duration_minutes=10)
    assert G.authorize(proposal(minutes=45), gctx(verdict=rule)).duration_minutes == 10
    assert G.authorize(proposal(IT.DELAY, 0), gctx()).duration_minutes == 2
    assert G.authorize(proposal(IT.SOFT_WARNING, 30), gctx()).duration_minutes == 0


def test_duplicate_active_intervention_is_suppressed():
    active = recent(IT.TEMPORARY_BLOCK, minutes_ago=3, active_for=15)
    r = G.authorize(proposal(IT.DELAY, 2), gctx(recent=[active]))
    assert RC.DUPLICATE_SUPPRESSED in r.flags and r.suppressed_duplicate_of == active.id
    assert r.decision is IT.TEMPORARY_BLOCK


def test_repeated_soft_warning_within_cooldown_is_dropped_but_escalation_allowed():
    earlier = recent(IT.SOFT_WARNING, minutes_ago=4)
    r = G.authorize(proposal(IT.SOFT_WARNING, 0), gctx(recent=[earlier]))
    assert r.decision is IT.ALLOW and RC.COOLDOWN in r.flags
    assert G.authorize(proposal(IT.DELAY, 2), gctx(recent=[earlier])).decision is IT.DELAY
    stale = recent(IT.SOFT_WARNING, minutes_ago=30)
    assert G.authorize(proposal(IT.SOFT_WARNING, 0), gctx(recent=[stale])).decision is IT.SOFT_WARNING


def test_hourly_rate_limit_and_daily_hard_budget():
    many = [recent(IT.SOFT_WARNING, minutes_ago=5 + i * 8, app=f"com.app.a{i}") for i in range(6)]
    r = G.authorize(proposal(IT.DELAY, 2), gctx(recent=many))
    assert r.decision is IT.ALLOW and RC.RATE_LIMITED in r.flags
    blocks = [recent(IT.TEMPORARY_BLOCK, minutes_ago=120 + i * 60, app=f"com.app.b{i}", status=InterventionStatus.EXPIRED)
              for i in range(8)]
    r = G.authorize(proposal(), gctx(recent=blocks))
    assert r.decision is IT.SOFT_WARNING and RC.BLOCK_BUDGET_EXHAUSTED in r.flags
    direct = PolicyVerdict(effect=PolicyEffect.BLOCK, winning_rule_id="r_b", escalation=Escalation.DIRECT)
    assert G.authorize(proposal(), gctx(recent=blocks, verdict=direct)).decision is IT.TEMPORARY_BLOCK


def test_limits_are_configurable():
    strict = GuardrailLimits(min_confidence_hard=0.95)
    assert G.authorize(proposal(confidence=0.9), gctx(limits=strict)).decision is IT.SOFT_WARNING
