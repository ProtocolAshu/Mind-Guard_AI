"""Machine-readable policy language ("Personal AI Constitution", section 12).

Natural-language rules compile into `PolicyRule` objects. The schema is strict
(`extra="forbid"`) so neither an LLM nor malicious content can smuggle extra
directives (e.g. `disable_guardian`) through a compiled policy.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import AppCategory, ContentCategory, PolicyEffect

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
_PACKAGE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$")
PACKAGE_PATTERN = _PACKAGE.pattern


class Escalation(StrEnum):
    DIRECT = "direct"  # apply the effect as written
    SOFT_THEN_BLOCK = "soft_then_block"  # nudge first, apply the effect if the user continues
    ADAPTIVE = "adaptive"  # the effect is a ceiling; learning may pick a softer intervention


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = Field(pattern=_HHMM)
    end: str = Field(pattern=_HHMM)
    days: list[int] = Field(default_factory=lambda: list(range(7)), description="0=Monday ... 6=Sunday")

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("days must not be empty")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be in 0..6")
        return sorted(set(v))

    @model_validator(mode="after")
    def _non_empty(self) -> TimeWindow:
        if self.start == self.end:
            raise ValueError("time window start and end must differ")
        return self

    @staticmethod
    def to_minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def contains(self, weekday: int, minute_of_day: int) -> bool:
        start, end = self.to_minutes(self.start), self.to_minutes(self.end)
        if start < end:
            return weekday in self.days and start <= minute_of_day < end
        # Overnight window (e.g. 23:00-05:00): the part after midnight belongs to the previous day's window.
        if minute_of_day >= start:
            return weekday in self.days
        if minute_of_day < end:
            return ((weekday - 1) % 7) in self.days
        return False


class RuleCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_window: TimeWindow | None = None
    focus_session: bool | None = None
    app_categories: list[AppCategory] = Field(default_factory=list, max_length=10)
    app_packages: list[str] = Field(default_factory=list, max_length=50)
    content_categories: list[ContentCategory] = Field(default_factory=list, max_length=14)
    min_session_minutes: int | None = Field(default=None, ge=1, le=600)
    min_daily_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("app_packages")
    @classmethod
    def _packages(cls, v: list[str]) -> list[str]:
        for p in v:
            if not _PACKAGE.match(p) or len(p) > 255:
                raise ValueError(f"invalid Android package name: {p!r}")
        return sorted(set(v))

    def specificity(self) -> int:
        score = 0
        score += 1 if self.time_window else 0
        score += 1 if self.focus_session is not None else 0
        score += 1 if self.app_categories else 0
        score += 2 if self.app_packages else 0
        score += 1 if self.content_categories else 0
        score += 1 if self.min_session_minutes else 0
        score += 1 if self.min_daily_minutes else 0
        return score


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(pattern=r"^r_[a-z0-9_]{2,40}$")
    description: str = Field(min_length=1, max_length=280)
    effect: PolicyEffect
    condition: RuleCondition = Field(default_factory=RuleCondition)
    escalation: Escalation = Escalation.SOFT_THEN_BLOCK
    max_duration_minutes: int | None = Field(default=None, ge=1, le=120)
    priority: int = Field(default=50, ge=0, le=100)
    source_text: str = Field(default="", max_length=500)


class GoalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)


class UnsupportedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=500)
    reason: str = Field(max_length=500)


class CompiledConstitution(BaseModel):
    """Output contract of the Goal Agent (deterministic parser or LLM)."""

    model_config = ConfigDict(extra="forbid")
    goal: GoalSpec | None = None
    rules: list[PolicyRule] = Field(default_factory=list, max_length=50)
    unsupported: list[UnsupportedRequest] = Field(default_factory=list, max_length=20)
    clarifications: list[str] = Field(default_factory=list, max_length=10)


class ValidationIssue(BaseModel):
    severity: str  # "error" | "warning"
    rule_id: str | None
    message: str


class ValidationReport(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class PolicyContext(BaseModel):
    """Everything the deterministic policy engine is allowed to look at."""

    weekday: int = Field(ge=0, le=6)
    minute_of_day: int = Field(ge=0, le=1439)
    focus_session_active: bool = False
    app_package: str | None = None
    app_category: AppCategory = AppCategory.OTHER
    content_category: ContentCategory = ContentCategory.UNKNOWN
    content_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    session_minutes: float = Field(default=0.0, ge=0.0)
    daily_minutes_by_app: dict[str, float] = Field(default_factory=dict)
    daily_minutes_by_category: dict[str, float] = Field(default_factory=dict)
    warned_this_session: bool = False


class PolicyVerdict(BaseModel):
    effect: PolicyEffect | None = None  # None = no rule matched
    winning_rule_id: str | None = None
    matched_rule_ids: list[str] = Field(default_factory=list)
    escalation: Escalation | None = None
    max_duration_minutes: int | None = None
    content_dependent: bool = False
    in_restricted_window: bool = False
    explanation: str = ""
