from app.policies.compiler import ConstitutionCompiler, goal_relevant_categories, summarize_constitution
from app.schemas.common import AppCategory, ContentCategory, PolicyEffect
from app.schemas.policy import Escalation

C = ConstitutionCompiler()


def rules_by_effect(result):
    out = {}
    for r in result.constitution.rules:
        out.setdefault(r.effect, []).append(r)
    return out


def test_section_4_2_example_goal_window_and_soft_then_block():
    res = C.compile("I am preparing for placements. Don't allow entertainment content between 8 AM and 11 AM.")
    assert res.constitution.goal.title == "Preparing for placements"
    (block,) = res.constitution.rules
    assert block.effect is PolicyEffect.BLOCK and block.escalation is Escalation.SOFT_THEN_BLOCK
    assert (block.condition.time_window.start, block.condition.time_window.end) == ("08:00", "11:00")
    assert ContentCategory.ENTERTAINMENT in block.condition.content_categories
    assert ContentCategory.EDUCATION not in block.condition.content_categories
    summary = summarize_constitution(res.constitution)
    assert summary["focus_windows"] == ["08:00-11:00"] and summary["intervention_preference"] == "soft_then_block"
    assert res.validation.valid and not res.unparsed


def test_demo_2_block_entertainment_8_to_11():
    res = C.compile("I am preparing for placements. Block entertainment from 8-11 AM.")
    (r,) = res.constitution.rules
    assert r.rule_id == "r_block_entertainment"
    assert (r.condition.time_window.start, r.condition.time_window.end) == ("08:00", "11:00")


def test_section_12_examples():
    res = C.compile(
        "I am preparing for placements. Don't allow short videos during study sessions. Allow educational YouTube. "
        "After 11 PM, block entertainment. If I exceed 45 minutes of social media, ask me before continuing."
    )
    rules = rules_by_effect(res)
    short = rules[PolicyEffect.BLOCK][0]
    assert short.condition.focus_session is True and short.condition.content_categories == [ContentCategory.SHORT_FORM_VIDEO]
    allow = rules[PolicyEffect.ALLOW][0]
    assert allow.condition.app_packages == ["com.google.android.youtube"]
    assert allow.condition.content_categories == [ContentCategory.EDUCATION]
    night = rules[PolicyEffect.BLOCK][1]
    assert (night.condition.time_window.start, night.condition.time_window.end) == ("23:00", "05:00")
    confirm = rules[PolicyEffect.REQUIRE_CONFIRMATION][0]
    assert confirm.condition.min_session_minutes == 45
    assert set(confirm.condition.app_categories) == {AppCategory.SOCIAL_MEDIA, AppCategory.VIDEO}
    assert res.validation.valid


def test_exception_clause_inherits_window():
    res = C.compile("Don't allow short videos between 8 AM and 11 AM, but educational YouTube is fine.")
    block, allow = res.constitution.rules
    assert allow.effect is PolicyEffect.ALLOW and allow.priority > block.priority
    assert allow.condition.time_window == block.condition.time_window


def test_except_weekends_and_doomscrolling_interpretation():
    (r,) = C.compile("Prevent doomscrolling after 11 PM except on weekends").constitution.rules
    assert r.condition.time_window.days == [0, 1, 2, 3, 4]
    assert r.condition.min_session_minutes == 15


def test_only_allow_generates_exception_and_restriction():
    allow, block = C.compile("Only allow educational content during study sessions").constitution.rules
    assert allow.effect is PolicyEffect.ALLOW and block.effect is PolicyEffect.BLOCK
    assert allow.condition.focus_session and block.condition.focus_session and allow.priority > block.priority


def test_goal_relevant_exception_added_for_app_level_restrictions():
    res = C.compile("I am preparing for placements. Don't allow social media during study sessions.")
    effects = [r.effect for r in res.constitution.rules]
    assert effects == [PolicyEffect.BLOCK, PolicyEffect.ALLOW]
    exception = res.constitution.rules[1]
    assert set(exception.condition.content_categories) == {ContentCategory.CAREER, ContentCategory.EDUCATION,
                                                          ContentCategory.TECHNOLOGY}
    assert any("r_allow_goal_relevant" in c for c in res.constitution.clarifications)


def test_limits_default_and_explicit_daily():
    res = C.compile("Allow LinkedIn but limit entertainment. Limit Instagram to 30 minutes a day.")
    limits = rules_by_effect(res)[PolicyEffect.LIMIT]
    assert limits[0].condition.min_daily_minutes == 30 and "30 minutes per day" in " ".join(res.constitution.clarifications)
    assert limits[1].condition.app_packages == ["com.instagram.android"] and limits[1].condition.min_daily_minutes == 30


def test_times_meridiem_inference_and_24h():
    (r,) = C.compile("Block reels between 10 and 2 pm").constitution.rules
    assert (r.condition.time_window.start, r.condition.time_window.end) == ("10:00", "14:00")
    (r,) = C.compile("Block reels strictly on weekdays from 9:30 to 17:00").constitution.rules
    assert r.condition.time_window.start == "09:30" and r.condition.time_window.days == [0, 1, 2, 3, 4]
    assert r.escalation is Escalation.DIRECT


def test_unsupported_and_essential_requests_never_become_rules():
    res = C.compile(
        "Read my girlfriend's WhatsApp messages. Block phone calls after 10pm. Uninstall TikTok. Block everything forever."
    )
    assert res.constitution.rules == []
    reasons = " ".join(u.reason for u in res.constitution.unsupported)
    assert "never reads private messages" in reasons and "Essential apps" in reasons
    assert "cannot uninstall" in reasons and "Device-wide" in reasons


def test_unparsed_text_is_reported():
    res = C.compile("lorem ipsum dolor sit amet")
    assert res.unparsed == ["lorem ipsum dolor sit amet"] and not res.constitution.rules


def test_goal_relevance_mapping():
    assert ContentCategory.CAREER in goal_relevant_categories("placement preparation")
    assert goal_relevant_categories("GATE exam") == [ContentCategory.EDUCATION]
    assert goal_relevant_categories("") == [ContentCategory.EDUCATION, ContentCategory.PRODUCTIVITY]
