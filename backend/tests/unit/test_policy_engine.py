import pytest
from pydantic import ValidationError

from app.policies.engine import evaluate_policy, explain_rule, validate_rules
from app.schemas.common import AppCategory, ContentCategory, PolicyEffect
from app.schemas.policy import Escalation, PolicyContext, PolicyRule, RuleCondition, TimeWindow

YT = "com.google.android.youtube"
IG = "com.instagram.android"


def rule(rule_id, effect, priority=50, **cond):
    return PolicyRule(rule_id=rule_id, description="t", effect=effect, condition=RuleCondition(**cond), priority=priority)


def ctx(**kw):
    base = {"weekday": 0, "minute_of_day": 9 * 60, "app_package": YT, "app_category": AppCategory.VIDEO}
    base.update(kw)
    return PolicyContext(**base)


def test_time_window_overnight_membership():
    w = TimeWindow(start="23:00", end="05:00", days=[4])  # Friday night
    assert w.contains(4, 23 * 60 + 30)
    assert w.contains(5, 2 * 60)  # Saturday 02:00 belongs to Friday's window
    assert not w.contains(5, 23 * 60 + 30)
    assert not w.contains(4, 2 * 60)


@pytest.mark.parametrize("bad", [{"start": "24:00", "end": "05:00"}, {"start": "08:00", "end": "08:00"},
                                 {"start": "8:00", "end": "09:00"}, {"start": "08:00", "end": "09:00", "days": [7]}])
def test_time_window_validation(bad):
    with pytest.raises(ValidationError):
        TimeWindow(**bad)


def test_policy_schema_forbids_smuggled_fields():
    with pytest.raises(ValidationError):
        PolicyRule.model_validate({"rule_id": "r_x_y", "description": "x", "effect": "ALLOW", "disable_guardian": True})
    with pytest.raises(ValidationError):
        RuleCondition(app_packages=["not a package; DROP TABLE"])
    with pytest.raises(ValidationError):
        PolicyRule(rule_id="bad id", description="x", effect=PolicyEffect.BLOCK)


def test_content_rule_matches_only_with_confident_content():
    rules = [rule("r_block_short", PolicyEffect.BLOCK, content_categories=[ContentCategory.SHORT_FORM_VIDEO],
                  time_window=TimeWindow(start="08:00", end="11:00"))]
    hit = evaluate_policy(rules, ctx(content_category=ContentCategory.SHORT_FORM_VIDEO, content_confidence=0.9))
    assert hit.effect is PolicyEffect.BLOCK and hit.winning_rule_id == "r_block_short"
    weak = evaluate_policy(rules, ctx(content_category=ContentCategory.SHORT_FORM_VIDEO, content_confidence=0.3))
    assert weak.effect is None and weak.content_dependent and weak.in_restricted_window
    unknown = evaluate_policy(rules, ctx())
    assert unknown.effect is None and unknown.content_dependent
    outside = evaluate_policy(rules, ctx(minute_of_day=12 * 60, content_category=ContentCategory.SHORT_FORM_VIDEO,
                                         content_confidence=0.9))
    assert outside.effect is None and not outside.in_restricted_window


def test_precedence_priority_then_specificity_then_narrow_window():
    block = rule("r_block_yt", PolicyEffect.BLOCK, app_packages=[YT], time_window=TimeWindow(start="08:00", end="11:00"))
    allow_edu = rule("r_allow_edu", PolicyEffect.ALLOW, priority=60, app_packages=[YT],
                     content_categories=[ContentCategory.EDUCATION])
    edu = ctx(content_category=ContentCategory.EDUCATION, content_confidence=0.8)
    verdict = evaluate_policy([block, allow_edu], edu)
    assert verdict.effect is PolicyEffect.ALLOW and verdict.matched_rule_ids == ["r_allow_edu", "r_block_yt"]
    narrow_allow = rule("r_allow_short_slot", PolicyEffect.ALLOW, app_packages=[YT],
                        time_window=TimeWindow(start="09:00", end="09:30"))
    assert evaluate_policy([block, narrow_allow], ctx(minute_of_day=9 * 60 + 10)).effect is PolicyEffect.ALLOW
    assert evaluate_policy([block, narrow_allow], ctx(minute_of_day=10 * 60)).effect is PolicyEffect.BLOCK


def test_thresholds_session_and_daily_by_category():
    confirm = rule("r_confirm_social", PolicyEffect.REQUIRE_CONFIRMATION, app_categories=[AppCategory.SOCIAL_MEDIA,
                   AppCategory.VIDEO], min_session_minutes=45)
    assert evaluate_policy([confirm], ctx(session_minutes=44)).effect is None
    assert evaluate_policy([confirm], ctx(session_minutes=45)).effect is PolicyEffect.REQUIRE_CONFIRMATION
    limit = rule("r_limit_ig", PolicyEffect.LIMIT, app_packages=[IG], min_daily_minutes=30)
    c = ctx(app_package=IG, app_category=AppCategory.SOCIAL_MEDIA, daily_minutes_by_app={IG: 31.0})
    assert evaluate_policy([limit], c).effect is PolicyEffect.LIMIT
    assert evaluate_policy([limit], c.model_copy(update={"daily_minutes_by_app": {IG: 10.0}})).effect is None


def test_focus_condition():
    r = rule("r_block_focus", PolicyEffect.BLOCK, focus_session=True, app_categories=[AppCategory.VIDEO])
    assert evaluate_policy([r], ctx(focus_session_active=True)).effect is PolicyEffect.BLOCK
    assert evaluate_policy([r], ctx(focus_session_active=False)).effect is None


def test_validation_errors_and_conflict_warnings():
    essential = rule("r_block_phone", PolicyEffect.BLOCK, app_packages=["com.google.android.dialer"])
    untargeted = rule("r_block_all", PolicyEffect.BLOCK, time_window=TimeWindow(start="08:00", end="10:00"))
    report = validate_rules([essential, untargeted, essential])
    messages = " | ".join(i.message for i in report.issues)
    assert not report.valid
    assert "essential" in messages and "no app" in messages and "duplicate" in messages
    block = rule("r_block_yt", PolicyEffect.BLOCK, app_packages=[YT], time_window=TimeWindow(start="08:00", end="11:00"))
    allow = rule("r_allow_video", PolicyEffect.ALLOW, app_categories=[AppCategory.VIDEO],
                 time_window=TimeWindow(start="10:00", end="12:00"))
    report = validate_rules([block, allow])
    assert report.valid
    assert any("can both apply" in i.message and "Mon 10:00" in i.message for i in report.issues)
    long_delay = PolicyRule(rule_id="r_delay_x", description="d", effect=PolicyEffect.DELAY, max_duration_minutes=30,
                            condition=RuleCondition(app_packages=[IG]))
    assert any("capped at 5" in i.message for i in validate_rules([long_delay]).issues)


def test_explain_rule_is_human_readable():
    r = PolicyRule(rule_id="r_block_short", description="x", effect=PolicyEffect.BLOCK, escalation=Escalation.SOFT_THEN_BLOCK,
                   condition=RuleCondition(content_categories=[ContentCategory.SHORT_FORM_VIDEO],
                                           time_window=TimeWindow(start="08:00", end="11:00", days=[0, 1, 2, 3, 4])))
    text = explain_rule(r)
    assert text.startswith("Block short form video content weekdays 08:00-11:00")
    assert "nudge first" in text
