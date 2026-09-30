"""Deterministic compiler for the Personal AI Constitution (section 12).

High-precision parsing of the common rule shapes (time windows, focus sessions,
apps, content categories, thresholds, escalation). Anything it cannot parse is
returned as `unparsed` so the Goal Agent can decide whether an LLM compile is
worth the cost; anything asking for a prohibited capability becomes an
`UnsupportedRequest` with a grounded reason instead of a rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.policies import catalog
from app.policies.engine import validate_rules
from app.schemas.common import ENTERTAINMENT_LIKE, AppCategory, ContentCategory, PolicyEffect
from app.schemas.policy import (
    CompiledConstitution,
    Escalation,
    GoalSpec,
    PolicyRule,
    RuleCondition,
    TimeWindow,
    UnsupportedRequest,
    ValidationReport,
)

SOCIAL_AND_VIDEO = [AppCategory.SOCIAL_MEDIA, AppCategory.VIDEO]
WEEKDAYS = [0, 1, 2, 3, 4]
ALL_DAYS = list(range(7))


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_TIME = r"(?:(?P<{p}h>\d{{1,2}})(?:[:.](?P<{p}m>[0-5]\d))?\s*(?P<{p}ap>am|pm)?|(?P<{p}w>noon|midnight))"
_NOT_DURATION = r"(?!\s*(?:min|minute|hour|hr|h\b|day|%|x\b|times))"
_RANGE = _rx(
    r"(?P<pre>\b(?:between|from)\s+)?" + _TIME.format(p="a") + r"\s*(?:-|–|—|\bto\b|\band\b|\buntil\b|\btill\b)\s*"
    + _TIME.format(p="b") + _NOT_DURATION
)
_AFTER = _rx(r"\b(?:after|past|from)\s+" + _TIME.format(p="a") + _NOT_DURATION + r"(?!\s*(?:-|–|to\b|and\b|until\b))")
_BEFORE = _rx(r"\b(?:before|until|till)\s+" + _TIME.format(p="a") + _NOT_DURATION)

_PERIODS: tuple[tuple[re.Pattern[str], str, str, list[int], str], ...] = (
    (_rx(r"\blate at night\b|\blate[- ]night\b|\bat night\b|\bnight[- ]?time\b|\bnights\b|\bbefore bed\b|\bbedtime\b"),
     "23:00", "05:00", ALL_DAYS, "Interpreted 'night' as 23:00-05:00."),
    (_rx(r"\bin the mornings?\b|\bmornings\b"), "06:00", "12:00", ALL_DAYS, "Interpreted 'morning' as 06:00-12:00."),
    (_rx(r"\bin the afternoons?\b|\bafternoons\b"), "12:00", "17:00", ALL_DAYS, "Interpreted 'afternoon' as 12:00-17:00."),
    (_rx(r"\bin the evenings?\b|\bevenings\b"), "18:00", "22:00", ALL_DAYS, "Interpreted 'evening' as 18:00-22:00."),
    (_rx(r"\bwork(?:ing)? hours\b|\boffice hours\b|\bat work\b"), "09:00", "17:00", WEEKDAYS,
     "Interpreted 'work hours' as weekdays 09:00-17:00."),
    (_rx(r"\bschool hours\b|\bclass(?:es)? hours\b|\bduring class(?:es)?\b|\bcollege hours\b|\bin class\b"), "08:00",
     "16:00", WEEKDAYS, "Interpreted 'class hours' as weekdays 08:00-16:00."),
)
_DAY_WORDS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
_WEEKDAY_RX = _rx(r"\bweekdays?\b|\bmonday (?:to|through|-) friday\b|\bmon-fri\b|\bworkdays?\b")
_WEEKEND_RX = _rx(r"\bweekends?\b|\bsaturdays? and sundays?\b")
_DAY_RX = _rx(r"\b(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b")

_DURATION = _rx(
    r"\b(?:more than|over|longer than|exceed(?:s|ing)?|beyond|past|after|at least|cross(?:es|ing)?|reach(?:es|ing)?)"
    r"\s+(?:my\s+)?(?P<n>\d+(?:\.\d+)?)\s*(?P<u>minutes?|mins?|m\b|hours?|hrs?|h\b)(?P<rest>[^,.;]{0,40})"
)
_LIMIT_TO = _rx(r"\bto\s+(?P<n>\d+(?:\.\d+)?)\s*(?P<u>minutes?|mins?|hours?|hrs?)\s*(?:a|per|each)\s+day\b")
_FOR_DURATION = _rx(r"\bfor\s+(?P<n>\d+)\s*(?P<u>minutes?|mins?|hours?|hrs?)\b")
_DAILY_HINT = _rx(r"\b(?:a|per|each)\s+day\b|\bdaily\b|\btoday\b|\bin total\b|\btotal\b")

_FOCUS = _rx(
    r"\b(?:during|while|when|in)\s+(?:my\s+|a\s+)?(?:i'?m\s+|i am\s+)?(?:study(?:ing)?|focus(?:ing)?|deep work)"
    r"(?:\s+(?:sessions?|time|hours|mode|blocks?|periods?))?\b|\bstudy (?:sessions?|time|hours|blocks?)\b"
    r"|\bfocus (?:sessions?|mode|time|blocks?)\b"
)
_GOAL = (
    _rx(r"\b(?:i am|i'm|im)\s+(?:currently\s+)?(?:preparing for|studying for|working on|training for|getting ready for|"
        r"focusing on|learning)\s+(?P<g>[^,.;!?]+)"),
    _rx(r"\bmy (?:main |current |top |big )?goal is (?:to )?(?P<g>[^,.;!?]+)"),
    _rx(r"\bi want to (?P<g>(?:crack|clear|pass|finish|prepare for|learn|get into|build|study for|improve)[^,.;!?]*)"),
)

_ONLY_ALLOW = _rx(r"\bonly\s+(?:allow|permit|let me (?:use|watch|see))\b")
_NEG_ALLOW = _rx(r"\b(?:don'?t|do not|never|not)\s+(?:allow|let me|permit)\b")
_EFFECTS: tuple[tuple[re.Pattern[str], PolicyEffect], ...] = (
    (_rx(r"\bask\s+(?:me\s+)?(?:before|first|whether|if|to confirm)\b|\bconfirm\b|\bcheck (?:in )?with me\b|\bask me\b"),
     PolicyEffect.REQUIRE_CONFIRMATION),
    (_rx(r"\bdelay\b|\bslow (?:me )?down\b|\bmake me wait\b|\b(?:pause|wait) before\b|\bcool[- ]?down\b"),
     PolicyEffect.DELAY),
    (_rx(r"\blimit\b|\brestrict\b|\bcap\b|\bat most\b|\bno more than\b|\bnot more than\b"), PolicyEffect.LIMIT),
    (_rx(r"\bblock\b|\bban\b|\bprevent\b|\bstop me\b|\bdisallow\b|\bforbid\b|\bshould ?n[o']t\b|\bshould not\b"
         r"|\bcut (?:me )?off\b|\bkeep me (?:off|away from)\b|\bno access\b"), PolicyEffect.BLOCK),
    (_rx(r"\bwarn\b|\bremind\b|\bnudge\b|\bnotify\b|\balert\b|\btell me\b|\blet me know\b"), PolicyEffect.WARN),
    (_rx(r"\b(?:allow(?:ed)?|permit(?:ted)?|whitelist(?:ed)?|let me (?:use|watch))\b"
         r"|\b(?:is|are|stays?|remains?)\s+(?:fine|ok|okay|allowed|permitted)\b|\bcan (?:use|watch)\b"), PolicyEffect.ALLOW),
)
_LEADING_NO = _rx(r"^\s*no\s+(?!more than|exceptions|later than|longer than)")

_APP_CATEGORY_WORDS: tuple[tuple[re.Pattern[str], list[AppCategory]], ...] = (
    (_rx(r"\bsocial[- ]?media\b|\bsocial (?:apps?|networks?|networking)\b|\bsocials\b"), SOCIAL_AND_VIDEO),
    (_rx(r"\bdoom ?scroll(?:ing)?\b|\bscroll(?:ing)?\b|\bfeeds?\b"), SOCIAL_AND_VIDEO),
    (_rx(r"\bvideo apps?\b|\bstreaming\b|\bbinge[- ]?watch(?:ing)?\b|\bvideo platforms?\b"), [AppCategory.VIDEO]),
    (_rx(r"\b(?:mobile )?games\b(?! streams?| videos?| content)|\bgaming apps?\b|\bplaying games\b"),
     [AppCategory.GAMES]),
    (_rx(r"\bmessaging apps?\b|\bchat apps?\b"), [AppCategory.MESSAGING]),
)
_CONTENT_WORDS: tuple[tuple[re.Pattern[str], list[ContentCategory]], ...] = (
    (_rx(r"\bshort[- ]?(?:form )?videos?\b|\bshorts\b|\breels?\b|\btik ?toks\b"), [ContentCategory.SHORT_FORM_VIDEO]),
    (_rx(r"\bentertainment\b|\bmemes?\b|\bfunny (?:videos|content|clips)\b|\bcomedy\b|\bcelebrity\b|\bgossip\b"),
     sorted(ENTERTAINMENT_LIKE, key=lambda c: c.value)),
    (_rx(r"\beducation(?:al)?\b|\blectures?\b|\btutorials?\b|\bcourses?\b|\bstudy (?:content|videos?|material)\b"
         r"|\blearning (?:content|videos?)\b|\bdocumentar(?:y|ies)\b"), [ContentCategory.EDUCATION]),
    (_rx(r"\bcareer (?:content|videos?|posts?)\b|\bjob (?:content|posts|search)\b|\binterview prep\b"),
     [ContentCategory.CAREER]),
    (_rx(r"\btech(?:nology)? (?:content|videos?|news)\b|\bprogramming (?:videos?|content)\b|\bcoding (?:videos?|content)\b"),
     [ContentCategory.TECHNOLOGY]),
    (_rx(r"\bnews\b|\bpolitics\b|\bpolitical content\b"), [ContentCategory.NEWS]),
    (_rx(r"\bads\b|\badvertis(?:ing|ements?)\b|\bsponsored (?:posts|content)\b"), [ContentCategory.ADVERTISING]),
    (_rx(r"\bgaming (?:videos?|content|streams?)\b|\bgame streams?\b|\blet'?s plays?\b"), [ContentCategory.GAMING]),
    (_rx(r"\bsports?\b|\bcricket\b|\bfootball\b|\bipl\b|\bmatch highlights\b"), [ContentCategory.SPORTS]),
    (_rx(r"\bclickbait\b|\brage[- ]?bait\b"), [ContentCategory.CLICKBAIT]),
)
_UNSUPPORTED: tuple[tuple[re.Pattern[str], str], ...] = (
    (_rx(r"\bread\b[^.]{0,30}\b(?:messages|chats|dms|whatsapp|texts|emails?|inbox)\b"),
     "MindGuard never reads private messages."),
    (_rx(r"\b(?:monitor|track|spy on|watch|read)\b[^.]{0,30}\b(?:friends?|girlfriend|boyfriend|partner|wife|husband|child|"
         r"kids?|son|daughter|brother|sister|roommate|someone else|other people|employees?)\b"),
     "MindGuard only protects your own attention on your own device; monitoring other people is not supported."),
    (_rx(r"\buninstall\b|\bdelete\b[^.]{0,20}\bapps?\b"),
     "MindGuard cannot uninstall apps; it only shows interventions you have authorized."),
    (_rx(r"\b(?:record|capture)\b[^.]{0,25}\b(?:screen|everything)\b|\bscreenshots? (?:all the time|automatically|continuously)\b"),
     "Continuous screen capture is not supported; you can share individual screenshots manually."),
    (_rx(r"\b(?:post|comment|like|reply)\b[^.]{0,20}\b(?:for me|on my behalf)\b"),
     "MindGuard never acts inside other apps on your behalf."),
    (_rx(r"\b(?:block|disable|lock)\b[^.]{0,25}\b(?:my phone|the phone|whole phone|everything|all apps|emergency calls?)\b"),
     "Device-wide lockouts are not supported; essential apps and emergency calls always stay available."),
    (_rx(r"\b(?:block|disable|limit|restrict|delay|ban)\b[^.]{0,25}\b(?:phone calls?|calls|sms|text messages|maps|"
         r"payments?|upi|alarms?|contacts)\b"),
     "Essential apps (calls, messages, maps, payments, alarms) can never be restricted."),
)
_FOREVER = _rx(r"\bforever\b|\bpermanently\b|\bfor good\b")
_ESC_DIRECT = _rx(r"\bstrict(?:ly)?\b|\bimmediately\b|\bhard block\b|\balways block\b|\bno exceptions\b|\bright away\b"
                  r"|\bstraight away\b|\bno warnings?\b")
_ESC_SOFT = _rx(r"\bgentl[ey]\b|\bsoft(?:ly)?\b|\bkindly\b|\b(?:warn|nudge) (?:me )?first\b")
_ESC_ADAPTIVE = _rx(r"\badaptive(?:ly)?\b|\bwhatever works\b|\bfigure out\b|\blearn what works\b|\bsmart(?:ly)?\b")

_GOAL_RELEVANCE: tuple[tuple[re.Pattern[str], list[ContentCategory]], ...] = (
    (_rx(r"placement|interview|job|career|internship|resume|hiring"),
     [ContentCategory.CAREER, ContentCategory.EDUCATION, ContentCategory.TECHNOLOGY]),
    (_rx(r"cod(?:e|ing)|programming|\bdsa\b|leetcode|codeforces|software|developer|machine learning|\bml\b|\bai\b|data"),
     [ContentCategory.TECHNOLOGY, ContentCategory.EDUCATION]),
    (_rx(r"exam|\bgate\b|\bjee\b|\bneet\b|\bupsc\b|\bcat\b|\bgre\b|test|semester|course|stud(?:y|ying)|class|thesis|research"),
     [ContentCategory.EDUCATION]),
    (_rx(r"startup|business|product"), [ContentCategory.CAREER, ContentCategory.TECHNOLOGY]),
)


def goal_relevant_categories(text: str) -> list[ContentCategory]:
    found: list[ContentCategory] = []
    for pattern, categories in _GOAL_RELEVANCE:
        if pattern.search(text or ""):
            found.extend(c for c in categories if c not in found)
    return found or [ContentCategory.EDUCATION, ContentCategory.PRODUCTIVITY]


@dataclass
class _Clause:
    text: str
    effect: PolicyEffect | None = None
    only_allow: bool = False
    packages: list[str] = field(default_factory=list)
    app_categories: list[AppCategory] = field(default_factory=list)
    content: list[ContentCategory] = field(default_factory=list)
    start: str | None = None
    end: str | None = None
    days: list[int] | None = None
    focus: bool | None = None
    min_session: int | None = None
    min_daily: int | None = None
    max_duration: int | None = None
    escalation: Escalation | None = None
    doomscroll: bool = False
    essential_only: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def has_target(self) -> bool:
        return bool(self.packages or self.app_categories or self.content)


@dataclass
class CompileResult:
    constitution: CompiledConstitution
    parsed: list[str]
    unparsed: list[str]
    goal_relevant: list[ContentCategory]
    validation: ValidationReport


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _conv(h: str | None, m: str | None, ap: str | None, word: str | None) -> int | None:
    if word:
        return 720 if word.lower() == "noon" else 0
    if h is None:
        return None
    hour, minute = int(h), int(m or 0)
    if hour > 24:
        return None
    if ap:
        ap = ap.lower()
        hour = hour % 12 + (12 if ap == "pm" else 0)
    return (hour % 24) * 60 + minute


def _parse_time(clause: _Clause) -> None:
    low = clause.text
    m = _RANGE.search(low)
    if m:
        explicit = bool(m.group("pre") or m.group("aap") or m.group("bap") or m.group("am") or m.group("bm")
                        or m.group("aw") or m.group("bw"))
        if explicit:
            a_ap, b_ap = m.group("aap"), m.group("bap")
            start = _conv(m.group("ah"), m.group("am"), a_ap, m.group("aw"))
            end = _conv(m.group("bh"), m.group("bm"), b_ap, m.group("bw"))
            if start is not None and end is not None:
                if a_ap is None and b_ap and m.group("ah") and not m.group("aw"):
                    start = _conv(m.group("ah"), m.group("am"), b_ap, None)
                    if start is not None and start >= end:
                        start = _conv(m.group("ah"), m.group("am"), "am" if b_ap.lower() == "pm" else "pm", None)
                elif not a_ap and not b_ap and not m.group("aw") and not m.group("bw"):
                    if int(m.group("ah")) <= 12 and int(m.group("bh")) <= 12 and end <= start:
                        end += 720
                    clause.notes.append(f"Read '{m.group(0).strip()}' as {_hhmm(start or 0)}-{_hhmm(end)}; add am/pm to be exact.")
                if start is not None and start % 1440 != end % 1440:
                    clause.start, clause.end = _hhmm(start), _hhmm(end)
                    return
    m = _AFTER.search(low)
    if m and (m.group("aap") or m.group("aw") or m.group("am") or re.search(r"\bafter\b", m.group(0), re.I)):
        start = _conv(m.group("ah"), m.group("am"), m.group("aap"), m.group("aw"))
        if start is not None:
            if not m.group("aap") and not m.group("aw") and m.group("ah") and int(m.group("ah")) <= 11:
                start = int(m.group("ah")) * 60 + 720 + int(m.group("am") or 0)
                clause.notes.append(f"Read 'after {m.group('ah')}' as {_hhmm(start)}.")
            end = 300 if (start >= 18 * 60 or start < 300) else 0
            if start < 300:
                end = 360
            clause.start, clause.end = _hhmm(start), _hhmm(end)
            clause.notes.append(f"'After {_hhmm(start)}' is applied until {_hhmm(end)}.")
            return
    m = _BEFORE.search(low)
    if m:
        end = _conv(m.group("ah"), m.group("am"), m.group("aap"), m.group("aw"))
        if end is not None:
            start = 18 * 60 if end == 0 else 0
            if start != end:
                clause.start, clause.end = _hhmm(start), _hhmm(end)
                clause.notes.append(f"'Before {_hhmm(end)}' is applied from {_hhmm(start)}.")
                return
    for pattern, start_s, end_s, days, note in _PERIODS:
        if pattern.search(low):
            clause.start, clause.end = start_s, end_s
            if days != ALL_DAYS and clause.days is None:
                clause.days = list(days)
            clause.notes.append(note)
            return


def _parse_days(clause: _Clause) -> None:
    low = clause.text
    if _WEEKDAY_RX.search(low):
        clause.days = list(WEEKDAYS)
    elif _WEEKEND_RX.search(low):
        clause.days = [5, 6]
    else:
        named = sorted({_DAY_WORDS[d.lower()] for d in _DAY_RX.findall(low)})
        if named:
            clause.days = named


def _minutes(n: str, unit: str) -> int:
    value = float(n)
    return round(value * 60 if unit.lower().startswith("h") else value)


def _parse_clause(text: str) -> _Clause:
    clause = _Clause(text=text.strip())
    low = clause.text
    if _ONLY_ALLOW.search(low):
        clause.effect, clause.only_allow = PolicyEffect.ALLOW, True
    elif _NEG_ALLOW.search(low) or _LEADING_NO.search(low):
        clause.effect = PolicyEffect.BLOCK
    else:
        for pattern, effect in _EFFECTS:
            if pattern.search(low):
                clause.effect = effect
                break
    clause.packages = [a.package for a in catalog.find_apps_in_text(low)]
    for pattern, cats in _APP_CATEGORY_WORDS:
        if pattern.search(low):
            clause.app_categories.extend(c for c in cats if c not in clause.app_categories)
            if "scroll" in pattern.pattern and re.search(r"doom ?scroll", low, re.I):
                clause.doomscroll = True
    for pattern, content in _CONTENT_WORDS:
        if pattern.search(low):
            clause.content.extend(c for c in content if c not in clause.content)
    # A package that is already covered by an explicitly named category is redundant, not wrong: keep both.
    if _FOCUS.search(low):
        clause.focus = True
    _parse_days(clause)
    _parse_time(clause)
    for m in _DURATION.finditer(low):
        minutes = _minutes(m.group("n"), m.group("u"))
        if _DAILY_HINT.search(m.group("rest") or "") or _DAILY_HINT.search(low):
            clause.min_daily = minutes
        else:
            clause.min_session = minutes
            clause.notes.append(
                f"'{m.group(0).strip()}' is applied to a single session; say 'per day' for a daily total."
            )
    limit_match = _LIMIT_TO.search(low)
    if limit_match:
        clause.min_daily = _minutes(limit_match.group("n"), limit_match.group("u"))
        clause.effect = clause.effect or PolicyEffect.LIMIT
    duration_match = _FOR_DURATION.search(low)
    if duration_match and clause.effect not in (None, PolicyEffect.ALLOW):
        clause.max_duration = min(120, _minutes(duration_match.group("n"), duration_match.group("u")))
    if _ESC_DIRECT.search(low):
        clause.escalation = Escalation.DIRECT
    elif _ESC_ADAPTIVE.search(low):
        clause.escalation = Escalation.ADAPTIVE
    elif _ESC_SOFT.search(low):
        clause.escalation = Escalation.SOFT_THEN_BLOCK
    return clause


_EXCEPTION_SPLIT = _rx(r",?\s*\b(but|except(?: for)?|however|although|though|apart from|other than)\b\s*")
_AND_SPLIT = _rx(
    r",?\s+and\s+(?=(?:also\s+)?(?:block|limit|warn|remind|delay|ask|allow|don'?t|do not|never|stop|prevent|nudge)\b)"
)


def _split_clauses(sentence: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    pieces = _EXCEPTION_SPLIT.split(sentence)
    # split() with one capture group returns [text, sep, text, sep, text...]
    first = True
    i = 0
    while i < len(pieces):
        chunk = pieces[i]
        is_exception = not first and i > 0
        for j, sub in enumerate(_AND_SPLIT.split(chunk)):
            if sub and sub.strip():
                parts.append((sub.strip(), is_exception and j == 0))
        first = False
        i += 2
    return parts


def _slug(text: str, limit: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:limit].rstrip("_")) or "rule"


class ConstitutionCompiler:
    def compile(self, text: str) -> CompileResult:
        normalized = re.sub(r"\b([ap])\.\s?m\.", r"\1m", text or "", flags=re.IGNORECASE)
        normalized = normalized.replace("’", "'").replace("‘", "'")
        sentences = [s.strip(" \t.;!?") for s in re.split(r"(?<=[.!?;])\s+|\n+", normalized) if s.strip(" \t.;!?")]
        goal: GoalSpec | None = None
        rules: list[PolicyRule] = []
        unsupported: list[UnsupportedRequest] = []
        clarifications: list[str] = []
        parsed: list[str] = []
        unparsed: list[str] = []
        used_ids: set[str] = set()

        for sentence in sentences[:40]:
            consumed = False
            blocked = False
            for pattern, reason in _UNSUPPORTED:
                if pattern.search(sentence):  # report every prohibited capability the sentence asks for
                    unsupported.append(UnsupportedRequest(text=sentence[:500], reason=reason))
                    blocked = consumed = True
            if blocked:
                parsed.append(sentence)
                continue
            if _FOREVER.search(sentence):
                clarifications.append(
                    "Restrictions are temporary by design (at most 60 minutes each); the rule re-applies instead."
                )
            for pattern in _GOAL:
                gm = pattern.search(sentence)
                if gm and goal is None:
                    title = gm.group(0).strip()
                    title = re.sub(r"^(?:i am|i'm|im)\s+(?:currently\s+)?", "", title, flags=re.IGNORECASE)
                    title = re.sub(r"^my (?:main |current |top |big )?goal is (?:to )?", "", title, flags=re.IGNORECASE)
                    title = re.sub(r"^i want to ", "", title, flags=re.IGNORECASE)
                    title = (title[:1].upper() + title[1:])[:120]
                    if len(title) >= 2:
                        goal = GoalSpec(title=title, description=sentence[:500])
                        consumed = True
                    break
            previous: _Clause | None = None
            previous_idx: list[int] = []
            for clause_text, is_exception in _split_clauses(sentence):
                clause = _parse_clause(clause_text)
                if is_exception and previous is not None:
                    only_days = clause.days is not None and clause.effect is None and not clause.has_target
                    if only_days and previous_idx:
                        for idx in previous_idx:
                            rules[idx] = self._exclude_days(rules[idx], clause.days or [])
                        clarifications.append("Excluded the named days from the preceding rule.")
                        consumed = True
                        continue
                    if clause.effect is None and clause.has_target and previous.effect not in (None, PolicyEffect.ALLOW):
                        clause.effect = PolicyEffect.ALLOW
                    if clause.effect is PolicyEffect.ALLOW:
                        clause.start = clause.start or previous.start
                        clause.end = clause.end or previous.end
                        clause.days = clause.days or previous.days
                        clause.focus = clause.focus if clause.focus is not None else previous.focus
                if clause.effect is None:
                    continue
                built = self._build(clause, used_ids)
                if clause.essential_only:
                    unsupported.append(
                        UnsupportedRequest(
                            text=clause.text[:500],
                            reason="Essential apps (calls, messages, maps, payments, alarms) can never be restricted.",
                        )
                    )
                    consumed = True
                    continue
                if not built:
                    continue
                if clause.effect is not PolicyEffect.ALLOW:
                    previous_idx = list(range(len(rules), len(rules) + len(built)))
                rules.extend(built)
                for rule in built:
                    used_ids.add(rule.rule_id)
                clarifications.extend(n for n in clause.notes if n not in clarifications)
                previous = clause
                consumed = True
            (parsed if consumed else unparsed).append(sentence)

        relevant = goal_relevant_categories(" ".join(filter(None, [goal.title if goal else "", normalized])))
        rules = self._add_goal_exceptions(rules, goal, relevant, used_ids, clarifications)
        constitution = CompiledConstitution(
            goal=goal, rules=rules[:50], unsupported=unsupported[:20], clarifications=clarifications[:10]
        )
        return CompileResult(constitution, parsed, unparsed, relevant, validate_rules(constitution.rules))

    @staticmethod
    def _exclude_days(rule: PolicyRule, days: list[int]) -> PolicyRule:
        window = rule.condition.time_window or TimeWindow(start="00:00", end="23:59")
        remaining = [d for d in window.days if d not in days] or window.days
        from app.policies.engine import explain_rule

        new_window = TimeWindow(start=window.start, end=window.end, days=remaining)
        cond = rule.condition.model_copy(update={"time_window": new_window})
        updated = rule.model_copy(update={"condition": cond})
        return updated.model_copy(update={"description": explain_rule(updated)})

    def _build(self, clause: _Clause, used: set[str]) -> list[PolicyRule]:
        notes = clause.notes
        window = None
        if clause.start and clause.end and clause.start != clause.end:
            window = TimeWindow(start=clause.start, end=clause.end, days=clause.days or ALL_DAYS)
        elif clause.days and clause.days != ALL_DAYS:
            window = TimeWindow(start="00:00", end="23:59", days=clause.days)
        packages = list(clause.packages)
        essential = [p for p in packages if catalog.is_essential(p)]
        if essential and clause.effect is not PolicyEffect.ALLOW:
            packages = [p for p in packages if p not in essential]
            if not (packages or clause.app_categories or clause.content):
                clause.essential_only = True
                return []
            notes.append("Essential apps (calls, messages, maps, payments) were left out: they are never restricted.")
        app_categories = list(clause.app_categories)
        content = list(clause.content)
        min_session = clause.min_session
        if clause.doomscroll and min_session is None and clause.effect is not PolicyEffect.ALLOW:
            min_session = 15
            notes.append("Interpreted 'doomscrolling' as social/video sessions longer than 15 minutes.")
        has_condition = bool(window or clause.focus is not None or min_session or clause.min_daily)

        if clause.only_allow:
            if not (packages or content):
                return []
            allow = self._rule(PolicyEffect.ALLOW, clause, used, packages, [], content, window, min_session, 60)
            block_cats = SOCIAL_AND_VIDEO
            block = self._rule(PolicyEffect.BLOCK, clause, used | {allow.rule_id}, [], block_cats, [], window, min_session, 50)
            notes.append("'Only allow' is applied as: allow the named content/apps, restrict other social and video apps.")
            return [allow, block]

        if not (packages or app_categories or content):
            if clause.effect is PolicyEffect.ALLOW or not has_condition:
                return []
            app_categories = list(SOCIAL_AND_VIDEO)
            notes.append("No app was named, so the rule applies to social-media and video apps.")
        if clause.effect is PolicyEffect.LIMIT and clause.min_daily is None and min_session is None:
            clause.min_daily = 30
            notes.append("No limit amount was given; using 30 minutes per day (edit the rule to change it).")
        priority = 60 if clause.effect is PolicyEffect.ALLOW else 50
        return [self._rule(clause.effect, clause, used, packages, app_categories, content, window, min_session, priority)]  # type: ignore[arg-type]

    def _rule(
        self,
        effect: PolicyEffect,
        clause: _Clause,
        used: set[str],
        packages: list[str],
        app_categories: list[AppCategory],
        content: list[ContentCategory],
        window: TimeWindow | None,
        min_session: int | None,
        priority: int,
    ) -> PolicyRule:
        from app.policies.engine import explain_rule

        target = (
            (catalog.lookup(packages[0]).name if packages and catalog.lookup(packages[0]) else None)  # type: ignore[union-attr]
            or ("entertainment" if ContentCategory.ENTERTAINMENT in content else (content[0].value if content else None))
            or (app_categories[0].value if app_categories else None)
            or "rule"
        )
        base = f"r_{effect.value.lower()}_{_slug(target)}"
        rule_id, n = base, 2
        while rule_id in used:
            rule_id = f"{base}_{n}"
            n += 1
        used.add(rule_id)
        escalation = clause.escalation or (
            Escalation.SOFT_THEN_BLOCK if effect in (PolicyEffect.BLOCK, PolicyEffect.LIMIT) else Escalation.DIRECT
        )
        if effect in (PolicyEffect.WARN, PolicyEffect.DELAY, PolicyEffect.REQUIRE_CONFIRMATION, PolicyEffect.ALLOW):
            escalation = Escalation.DIRECT
        condition = RuleCondition(
            time_window=window,
            focus_session=clause.focus,
            app_categories=app_categories,
            app_packages=packages,
            content_categories=content,
            min_session_minutes=min_session,
            min_daily_minutes=clause.min_daily,
        )
        draft = PolicyRule(
            rule_id=rule_id,
            description="draft",
            effect=effect,
            condition=condition,
            escalation=escalation,
            max_duration_minutes=clause.max_duration if effect not in (PolicyEffect.ALLOW, PolicyEffect.WARN) else None,
            priority=priority,
            source_text=clause.text[:500],
        )
        return draft.model_copy(update={"description": explain_rule(draft)})

    def _add_goal_exceptions(
        self,
        rules: list[PolicyRule],
        goal: GoalSpec | None,
        relevant: list[ContentCategory],
        used: set[str],
        clarifications: list[str],
    ) -> list[PolicyRule]:
        from app.policies.engine import explain_rule

        out = list(rules)
        allowed_content = {c for r in rules if r.effect is PolicyEffect.ALLOW for c in r.condition.content_categories}
        for rule in rules:
            cond = rule.condition
            app_targeted = bool(cond.app_packages or cond.app_categories)
            goal_scoped = goal is not None or cond.focus_session is True
            if rule.effect is PolicyEffect.ALLOW or cond.content_categories or not app_targeted or not goal_scoped:
                continue
            missing = [c for c in relevant if c not in allowed_content]
            if not missing:
                continue
            rule_id, n = "r_allow_goal_relevant", 2
            while rule_id in used:
                rule_id = f"r_allow_goal_relevant_{n}"
                n += 1
            used.add(rule_id)
            exception = PolicyRule(
                rule_id=rule_id,
                description="draft",
                effect=PolicyEffect.ALLOW,
                condition=cond.model_copy(update={"content_categories": missing, "min_session_minutes": None,
                                                  "min_daily_minutes": None}),
                escalation=Escalation.DIRECT,
                priority=55,
                source_text="implicit: goal-relevant content exception",
            )
            out.append(exception.model_copy(update={"description": explain_rule(exception)}))
            allowed_content.update(missing)
            clarifications.append(
                f"Added exception {rule_id}: {', '.join(c.value for c in missing)} content stays allowed because it "
                "serves your goal. Remove it for a strict block."
            )
        return out


def summarize_constitution(c: CompiledConstitution) -> dict[str, Any]:
    """Compact view matching the structured-policy example of section 4.2."""
    restrictive = [r for r in c.rules if r.effect is not PolicyEffect.ALLOW]
    allows = [r for r in c.rules if r.effect is PolicyEffect.ALLOW]
    return {
        "goal": _slug(c.goal.title, 60) if c.goal else None,
        "focus_windows": sorted(
            {f"{r.condition.time_window.start}-{r.condition.time_window.end}" for r in c.rules if r.condition.time_window}
        ),
        "during_focus_sessions": any(r.condition.focus_session for r in c.rules),
        "restricted_content": sorted({cat.value for r in restrictive for cat in r.condition.content_categories}),
        "restricted_apps": sorted(
            {p for r in restrictive for p in r.condition.app_packages}
            | {cat.value for r in restrictive for cat in r.condition.app_categories}
        ),
        "allowed_exceptions": sorted(
            {cat.value for r in allows for cat in r.condition.content_categories}
            | {p for r in allows for p in r.condition.app_packages}
        ),
        "intervention_preference": restrictive[0].escalation.value if restrictive else None,
    }
