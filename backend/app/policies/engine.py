"""Deterministic policy evaluation and constitution validation.

The engine only ever *reads* compiled rules. Nothing produced by content analysis
or by an LLM is accepted here unless it first passed `CompiledConstitution`
schema validation and `validate_rules`, and was saved by the user.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.interfaces import PolicyEngine
from app.policies import catalog
from app.schemas.common import (
    MAX_DURATION_MINUTES,
    AppCategory,
    ContentCategory,
    InterventionType,
    PolicyEffect,
)
from app.schemas.policy import (
    Escalation,
    PolicyContext,
    PolicyRule,
    PolicyVerdict,
    RuleCondition,
    TimeWindow,
    ValidationIssue,
    ValidationReport,
)

CONTENT_MATCH_MIN_CONFIDENCE = 0.55
MINUTES_PER_WEEK = 7 * 1440
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

EFFECT_SEVERITY: dict[PolicyEffect, int] = {
    PolicyEffect.ALLOW: 0,
    PolicyEffect.WARN: 1,
    PolicyEffect.DELAY: 2,
    PolicyEffect.REQUIRE_CONFIRMATION: 2,
    PolicyEffect.LIMIT: 3,
    PolicyEffect.BLOCK: 4,
}
EFFECT_INTERVENTION: dict[PolicyEffect, InterventionType] = {
    PolicyEffect.ALLOW: InterventionType.ALLOW,
    PolicyEffect.WARN: InterventionType.SOFT_WARNING,
    PolicyEffect.DELAY: InterventionType.DELAY,
    PolicyEffect.REQUIRE_CONFIRMATION: InterventionType.REQUEST_CONFIRMATION,
    PolicyEffect.LIMIT: InterventionType.LIMITED_ACCESS,
    PolicyEffect.BLOCK: InterventionType.TEMPORARY_BLOCK,
}
RESTRICTIVE_EFFECTS = frozenset({PolicyEffect.DELAY, PolicyEffect.LIMIT, PolicyEffect.BLOCK})
SOCIAL_CATEGORY_KEYS = (AppCategory.SOCIAL_MEDIA.value, AppCategory.VIDEO.value)


@dataclass(frozen=True)
class RuleMatch:
    rule: PolicyRule
    matched: bool
    scope_active: bool  # time/focus/app conditions hold (thresholds or content may still be pending)
    content_uncertain: bool  # would match if the content category were confirmed
    satisfied: tuple[str, ...] = field(default_factory=tuple)


def window_minutes(window: TimeWindow | None) -> int:
    if window is None:
        return 1440
    start, end = TimeWindow.to_minutes(window.start), TimeWindow.to_minutes(window.end)
    return end - start if start < end else 1440 - start + end


def _app_scope(cond: RuleCondition, ctx: PolicyContext) -> bool | None:
    if not cond.app_packages and not cond.app_categories:
        return None
    if ctx.app_package and ctx.app_package in cond.app_packages:
        return True
    return ctx.app_category in cond.app_categories


def daily_minutes_for(cond: RuleCondition, ctx: PolicyContext) -> float:
    if cond.app_packages or cond.app_categories:
        total = sum(ctx.daily_minutes_by_app.get(p, 0.0) for p in cond.app_packages)
        categories = {c.value for c in cond.app_categories}
        # Avoid double counting a package that also belongs to a targeted category.
        pkg_cats = {catalog.category_of(p).value for p in cond.app_packages}
        total += sum(v for k, v in ctx.daily_minutes_by_category.items() if k in categories and k not in pkg_cats)
        return total
    return sum(ctx.daily_minutes_by_category.get(k, 0.0) for k in SOCIAL_CATEGORY_KEYS)


def match_rule(rule: PolicyRule, ctx: PolicyContext) -> RuleMatch:
    cond = rule.condition
    satisfied: list[str] = []
    if cond.time_window is not None:
        if not cond.time_window.contains(ctx.weekday, ctx.minute_of_day):
            return RuleMatch(rule, False, False, False)
        satisfied.append(f"time window {cond.time_window.start}-{cond.time_window.end}")
    if cond.focus_session is not None:
        if cond.focus_session != ctx.focus_session_active:
            return RuleMatch(rule, False, False, False)
        satisfied.append("focus session active" if cond.focus_session else "no focus session")
    app_ok = _app_scope(cond, ctx)
    if app_ok is False:
        return RuleMatch(rule, False, False, False)
    if app_ok:
        satisfied.append(f"app {ctx.app_package or ctx.app_category.value}")

    thresholds_ok = True
    if cond.min_session_minutes is not None:
        if ctx.session_minutes >= cond.min_session_minutes:
            satisfied.append(f"session ≥ {cond.min_session_minutes} min")
        else:
            thresholds_ok = False
    if cond.min_daily_minutes is not None:
        if daily_minutes_for(cond, ctx) >= cond.min_daily_minutes:
            satisfied.append(f"daily usage ≥ {cond.min_daily_minutes} min")
        else:
            thresholds_ok = False

    content_ok = True
    content_uncertain = False
    if cond.content_categories:
        in_list = ctx.content_category in cond.content_categories
        if in_list and ctx.content_confidence >= CONTENT_MATCH_MIN_CONFIDENCE:
            satisfied.append(f"content {ctx.content_category.value}")
        else:
            content_ok = False
            content_uncertain = ctx.content_category is ContentCategory.UNKNOWN or (
                in_list and ctx.content_confidence < CONTENT_MATCH_MIN_CONFIDENCE
            )
    matched = thresholds_ok and content_ok
    return RuleMatch(rule, matched, True, content_uncertain and thresholds_ok, tuple(satisfied))


def precedence_key(rule: PolicyRule) -> tuple[int, int, int, int]:
    """Sort key (ascending = wins): priority, specificity, narrower window, then more restrictive."""
    return (
        -rule.priority,
        -rule.condition.specificity(),
        window_minutes(rule.condition.time_window),
        -EFFECT_SEVERITY[rule.effect],
    )


def evaluate_policy(rules: Sequence[PolicyRule], ctx: PolicyContext) -> PolicyVerdict:
    matches = [match_rule(r, ctx) for r in rules]
    matched = sorted((m for m in matches if m.matched), key=lambda m: precedence_key(m.rule))
    restrictive_scoped = [m for m in matches if m.scope_active and m.rule.effect is not PolicyEffect.ALLOW]
    in_window = any(m.rule.condition.time_window is not None or m.rule.condition.focus_session for m in restrictive_scoped)
    if not matched:
        uncertain = [m for m in restrictive_scoped if m.content_uncertain]
        explanation = "No policy rule applies right now."
        if uncertain:
            explanation = (
                f"Rule {uncertain[0].rule.rule_id} would apply if the content is "
                f"{', '.join(c.value for c in uncertain[0].rule.condition.content_categories)}; content is not confirmed."
            )
        return PolicyVerdict(
            effect=None,
            content_dependent=bool(uncertain),
            in_restricted_window=in_window,
            explanation=explanation,
        )
    winner = matched[0].rule
    reasons = "; ".join(matched[0].satisfied) or "unconditional"
    explanation = f"Rule {winner.rule_id} ({winner.effect.value}) applies: {reasons}."
    if len(matched) > 1:
        explanation += f" It takes precedence over {', '.join(m.rule.rule_id for m in matched[1:])}."
    return PolicyVerdict(
        effect=winner.effect,
        winning_rule_id=winner.rule_id,
        matched_rule_ids=[m.rule.rule_id for m in matched],
        escalation=winner.escalation,
        max_duration_minutes=winner.max_duration_minutes,
        content_dependent=False,
        in_restricted_window=in_window or winner.effect is not PolicyEffect.ALLOW,
        explanation=explanation,
    )


# --------------------------------------------------------------------------- explanation


def _format_days(days: Iterable[int]) -> str:
    d = sorted(set(days))
    if d == list(range(7)):
        return "every day"
    if d == [0, 1, 2, 3, 4]:
        return "weekdays"
    if d == [5, 6]:
        return "weekends"
    return ", ".join(DAY_NAMES[i] for i in d)


_EFFECT_VERB = {
    PolicyEffect.ALLOW: "Allow",
    PolicyEffect.WARN: "Warn about",
    PolicyEffect.DELAY: "Delay",
    PolicyEffect.REQUIRE_CONFIRMATION: "Ask for confirmation before",
    PolicyEffect.LIMIT: "Limit",
    PolicyEffect.BLOCK: "Block",
}
_ESCALATION_TEXT = {
    Escalation.DIRECT: "applied directly",
    Escalation.SOFT_THEN_BLOCK: "nudge first, then enforce if you continue",
    Escalation.ADAPTIVE: "MindGuard picks the gentlest intervention that works for you, up to this limit",
}


def explain_rule(rule: PolicyRule) -> str:
    cond = rule.condition
    targets: list[str] = []
    if cond.content_categories:
        targets.append(" / ".join(c.value.replace("_", " ") for c in cond.content_categories) + " content")
    apps = [(catalog.lookup(p).name if catalog.lookup(p) else p) for p in cond.app_packages]  # type: ignore[union-attr]
    apps += [c.value.replace("_", " ") + " apps" for c in cond.app_categories]
    if apps:
        targets.append(("on " if cond.content_categories else "") + ", ".join(apps))
    text = f"{_EFFECT_VERB[rule.effect]} {' '.join(targets) if targets else 'non-essential apps'}"
    when: list[str] = []
    if cond.time_window:
        when.append(f"{_format_days(cond.time_window.days)} {cond.time_window.start}-{cond.time_window.end}")
    if cond.focus_session is True:
        when.append("during focus sessions")
    if cond.min_session_minutes:
        when.append(f"once a session passes {cond.min_session_minutes} min")
    if cond.min_daily_minutes:
        when.append(f"after {cond.min_daily_minutes} min in a day")
    if when:
        text += " " + ", ".join(when)
    if rule.effect in RESTRICTIVE_EFFECTS:
        text += f" ({_ESCALATION_TEXT[rule.escalation]}"
        if rule.max_duration_minutes:
            text += f"; up to {rule.max_duration_minutes} min"
        text += ")"
    return text[:280]


# --------------------------------------------------------------------------- validation


def weekly_intervals(window: TimeWindow | None) -> list[tuple[int, int]]:
    if window is None:
        return [(0, MINUTES_PER_WEEK)]
    start, end = TimeWindow.to_minutes(window.start), TimeWindow.to_minutes(window.end)
    out: list[tuple[int, int]] = []
    for day in window.days:
        base = day * 1440
        if start < end:
            out.append((base + start, base + end))
        else:
            out.append((base + start, base + 1440))
            nxt = ((day + 1) % 7) * 1440
            out.append((nxt, nxt + end))
    return out


def _first_overlap(a: TimeWindow | None, b: TimeWindow | None) -> tuple[int, int] | None:
    for x0, x1 in weekly_intervals(a):
        for y0, y1 in weekly_intervals(b):
            lo, hi = max(x0, y0), min(x1, y1)
            if lo < hi:
                return lo, hi
    return None


def _app_targets_overlap(a: RuleCondition, b: RuleCondition) -> bool:
    if not (a.app_packages or a.app_categories) or not (b.app_packages or b.app_categories):
        return True
    if set(a.app_packages) & set(b.app_packages) or set(a.app_categories) & set(b.app_categories):
        return True
    return any(catalog.category_of(p) in b.app_categories for p in a.app_packages) or any(
        catalog.category_of(p) in a.app_categories for p in b.app_packages
    )


def rules_overlap(a: PolicyRule, b: PolicyRule) -> tuple[int, int] | None:
    ca, cb = a.condition, b.condition
    if ca.focus_session is not None and cb.focus_session is not None and ca.focus_session != cb.focus_session:
        return None
    if ca.content_categories and cb.content_categories and not set(ca.content_categories) & set(cb.content_categories):
        return None
    if not _app_targets_overlap(ca, cb):
        return None
    return _first_overlap(ca.time_window, cb.time_window)


def _fmt_week_minute(m: int) -> str:
    day, minute = divmod(m % MINUTES_PER_WEEK, 1440)
    return f"{DAY_NAMES[day]} {minute // 60:02d}:{minute % 60:02d}"


def validate_rules(rules: Sequence[PolicyRule]) -> ValidationReport:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for rule in rules:
        cond = rule.condition
        if rule.rule_id in seen:
            issues.append(ValidationIssue(severity="error", rule_id=rule.rule_id, message="duplicate rule_id"))
        seen.add(rule.rule_id)
        targeted = bool(cond.app_packages or cond.app_categories or cond.content_categories)
        if rule.effect is not PolicyEffect.ALLOW:
            essential = [p for p in cond.app_packages if catalog.is_essential(p)]
            if essential or AppCategory.ESSENTIAL in cond.app_categories:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        rule_id=rule.rule_id,
                        message="essential apps (calls, messages, maps, payments, emergency) can never be restricted",
                    )
                )
            if not targeted:
                issues.append(
                    ValidationIssue(
                        severity="error" if rule.effect in RESTRICTIVE_EFFECTS else "warning",
                        rule_id=rule.rule_id,
                        message="rule has no app, app-category or content target"
                        + ("" if rule.effect not in RESTRICTIVE_EFFECTS else "; restrictive rules must be targeted"),
                    )
                )
        if rule.max_duration_minutes is not None:
            intervention = EFFECT_INTERVENTION[rule.effect]
            cap = MAX_DURATION_MINUTES[intervention]
            if cap == 0:
                issues.append(
                    ValidationIssue(
                        severity="warning", rule_id=rule.rule_id, message="max_duration_minutes is ignored for this effect"
                    )
                )
            elif rule.max_duration_minutes > cap:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        rule_id=rule.rule_id,
                        message=f"max_duration_minutes will be capped at {cap} for {intervention.value}",
                    )
                )
    for i, a in enumerate(rules):
        for b in rules[i + 1 :]:
            if a.effect == b.effect:
                continue
            conflicting = PolicyEffect.ALLOW in (a.effect, b.effect) or abs(
                EFFECT_SEVERITY[a.effect] - EFFECT_SEVERITY[b.effect]
            ) >= 2
            if not conflicting:
                continue
            overlap = rules_overlap(a, b)
            if overlap is None:
                continue
            winner, loser = sorted((a, b), key=precedence_key)
            issues.append(
                ValidationIssue(
                    severity="warning",
                    rule_id=winner.rule_id,
                    message=(
                        f"{a.rule_id} ({a.effect.value}) and {b.rule_id} ({b.effect.value}) can both apply "
                        f"(e.g. from {_fmt_week_minute(overlap[0])}); {winner.rule_id} wins by "
                        f"{_precedence_reason(winner, loser)}"
                    ),
                )
            )
    return ValidationReport(valid=not any(i.severity == "error" for i in issues), issues=issues)


def _precedence_reason(winner: PolicyRule, loser: PolicyRule) -> str:
    if winner.priority != loser.priority:
        return "higher priority"
    if winner.condition.specificity() != loser.condition.specificity():
        return "being more specific"
    if window_minutes(winner.condition.time_window) != window_minutes(loser.condition.time_window):
        return "its narrower time window"
    return "being more restrictive"


class DeterministicPolicyEngine(PolicyEngine):
    """Default PolicyEngine: the typed rule DSL evaluated by the pure functions above."""

    name = "deterministic-dsl"

    def evaluate(self, rules: Sequence[PolicyRule], ctx: PolicyContext) -> PolicyVerdict:
        return evaluate_policy(rules, ctx)

    def validate(self, rules: Sequence[PolicyRule]) -> ValidationReport:
        return validate_rules(rules)

    def explain(self, rule: PolicyRule) -> str:
        return explain_rule(rule)
