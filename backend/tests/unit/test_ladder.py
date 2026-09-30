import pytest

from app.decision.ladder import LadderInput, recommend
from app.schemas.agents import RiskAssessment
from app.schemas.common import ContentCategory, InterventionStyle, PolicyEffect, ReasonCode, RiskMethod
from app.schemas.common import InterventionType as IT
from app.schemas.policy import Escalation, PolicyRule, PolicyVerdict, RuleCondition


def risk(p, conf=0.8):
    return RiskAssessment(distraction_risk=p, doomscroll_probability=p, goal_conflict=0.5, intervention_urgency=p,
                          continuation_probability=p, predicted_productivity_loss_minutes=5, confidence=conf,
                          method=RiskMethod.RULES)


def ladder(p=0.8, verdict=None, warned=False, preferred=None, focus=False, rule=None, content_conf=0.9):
    return recommend(LadderInput(risk=risk(p), verdict=verdict or PolicyVerdict(), winning_rule=rule,
                                 style=InterventionStyle.BALANCED, warned_this_session=warned, focus_active=focus,
                                 has_goal=True, content_category=ContentCategory.UNKNOWN,
                                 content_confidence=content_conf, preferred=preferred))


def block(escalation):
    return PolicyVerdict(effect=PolicyEffect.BLOCK, winning_rule_id="r_block_x", escalation=escalation, explanation="rule")


def test_direct_block_rule_leaves_no_room_for_personalisation():
    out = ladder(verdict=block(Escalation.DIRECT), preferred=IT.DELAY)
    assert out.recommended is IT.TEMPORARY_BLOCK and out.eligible == (IT.TEMPORARY_BLOCK,)
    assert ReasonCode.POLICY_RULE_MATCHED in out.reason_codes and out.confidence >= 0.75


def test_soft_then_block_nudges_then_enforces():
    first = ladder(verdict=block(Escalation.SOFT_THEN_BLOCK))
    assert first.stage == "policy-nudge" and first.recommended is IT.MINDFUL_PROMPT
    assert IT.TEMPORARY_BLOCK not in first.eligible
    second = ladder(verdict=block(Escalation.SOFT_THEN_BLOCK), warned=True)
    assert second.stage == "policy-enforce" and second.recommended is IT.TEMPORARY_BLOCK
    assert set(second.eligible) == {IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK}


def test_memory_preference_only_acts_inside_the_policy_bounded_set():
    enforce = ladder(verdict=block(Escalation.SOFT_THEN_BLOCK), warned=True, preferred=IT.LIMITED_ACCESS)
    assert enforce.recommended is IT.LIMITED_ACCESS and ReasonCode.MEMORY_PATTERN in enforce.reason_codes
    # A remembered "block me" preference cannot escalate a WARN-only policy (section 45).
    warn = ladder(verdict=PolicyVerdict(effect=PolicyEffect.WARN, winning_rule_id="r_w"), preferred=IT.TEMPORARY_BLOCK)
    assert warn.recommended is IT.SOFT_WARNING and IT.TEMPORARY_BLOCK not in warn.eligible
    # A remembered preference cannot jump more than one severity level above the recommendation.
    low = ladder(p=0.5, preferred=IT.DELAY)
    assert low.recommended is not IT.DELAY


def test_policy_exception_allows():
    out = ladder(verdict=PolicyVerdict(effect=PolicyEffect.ALLOW, winning_rule_id="r_allow_edu"))
    assert out.recommended is IT.ALLOW and out.eligible == (IT.ALLOW,)


def test_uncertain_content_inside_restricted_window_asks_instead_of_blocking():
    verdict = PolicyVerdict(effect=None, content_dependent=True, in_restricted_window=True, explanation="maybe")
    out = ladder(verdict=verdict)
    assert out.recommended is IT.REQUEST_CONFIRMATION and ReasonCode.CONTENT_UNCERTAIN in out.reason_codes
    assert not {IT.TEMPORARY_BLOCK, IT.LIMITED_ACCESS} & set(out.eligible)


@pytest.mark.parametrize(("p", "expected"), [(0.2, IT.ALLOW), (0.5, IT.SOFT_WARNING), (0.65, IT.MINDFUL_PROMPT),
                                             (0.9, IT.DELAY)])
def test_risk_only_bands(p, expected):
    assert ladder(p=p).recommended is expected


def test_hard_blocks_without_a_rule_require_focus_session():
    assert IT.TEMPORARY_BLOCK not in ladder(p=0.9).eligible
    assert IT.TEMPORARY_BLOCK in ladder(p=0.9, focus=True).eligible


def test_content_rule_confidence_is_bounded_by_content_confidence():
    rule = PolicyRule(rule_id="r_block_short", description="d", effect=PolicyEffect.BLOCK,
                      condition=RuleCondition(content_categories=[ContentCategory.SHORT_FORM_VIDEO]))
    out = ladder(verdict=block(Escalation.DIRECT), rule=rule, content_conf=0.4)
    assert out.confidence == pytest.approx(0.55)
