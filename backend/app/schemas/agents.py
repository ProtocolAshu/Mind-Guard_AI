"""Typed contracts exchanged between agents — the explicit state model (section 22).

Every agent reads and writes these models only, so each transition in the graph
is serialisable, observable (persisted as `agent_steps`) and testable in isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import (
    AppCategory,
    ContentCategory,
    ContentSource,
    DecisionSource,
    GoalRelevance,
    InterventionStatus,
    InterventionType,
    MemoryType,
    OutcomeType,
    OverrideKind,
    ReasonCode,
    RiskMethod,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceCapabilities(_Strict):
    """What the user's device has granted. `None` = not reported (treated as available)."""

    usage_access: bool | None = None
    notifications: bool | None = None
    overlay: bool | None = None
    dnd_access: bool | None = None

    def can_notify(self) -> bool:
        return self.notifications is not False

    def can_overlay(self) -> bool:
        return self.overlay is not False


class OverrideSnapshot(_Strict):
    id: uuid.UUID
    kind: OverrideKind
    app_package: str | None = None
    expires_at: datetime | None = None


class RecentIntervention(_Strict):
    id: uuid.UUID
    decision: InterventionType
    app_package: str | None = None
    status: InterventionStatus
    created_at: datetime
    expires_at: datetime | None = None


class GoalSnapshot(_Strict):
    id: uuid.UUID | None = None
    title: str
    description: str = ""
    priority: int = 3
    is_temporary: bool = False
    relevant_categories: list[ContentCategory] = Field(default_factory=list)


class ContextSnapshot(_Strict):
    now_utc: datetime
    local_time: str
    timezone: str
    weekday: int = Field(ge=0, le=6)
    minute_of_day: int = Field(ge=0, le=1439)
    is_night: bool
    is_weekend: bool
    app_package: str | None = None
    app_name: str | None = None
    app_category: AppCategory = AppCategory.OTHER
    session_id: uuid.UUID | None = None
    session_minutes: float = Field(default=0.0, ge=0.0)
    scroll_events: int = Field(default=0, ge=0)
    opens_last_hour: int = Field(default=0, ge=0)
    daily_social_minutes: float = Field(default=0.0, ge=0.0)
    daily_minutes_by_app: dict[str, float] = Field(default_factory=dict)
    daily_minutes_by_category: dict[str, float] = Field(default_factory=dict)
    daily_limit_minutes: int | None = None
    focus_session_active: bool = False
    active_goals: list[GoalSnapshot] = Field(default_factory=list)
    minutes_since_last_intervention: float | None = None
    warned_this_session: bool = False
    interventions_today: int = 0
    hard_interventions_today: int = 0
    risk_history: Literal["low", "medium", "high", "unknown"] = "unknown"


class BehaviorProfile(_Strict):
    days_observed: int = 0
    avg_daily_social_minutes: float = 0.0
    avg_session_minutes: float = 0.0
    long_session_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_opens_per_day: float = 0.0
    trigger_hours: list[int] = Field(default_factory=list)
    night_usage_share: float = Field(default=0.0, ge=0.0, le=1.0)
    intervention_counts: dict[str, int] = Field(default_factory=dict)
    acceptance_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    override_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    effectiveness_by_intervention: dict[str, float] = Field(default_factory=dict)
    user_level_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    patterns: list[str] = Field(default_factory=list)


class ContentAssessment(_Strict):
    category: ContentCategory = ContentCategory.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_relevance: GoalRelevance = GoalRelevance.NEUTRAL
    source: ContentSource = ContentSource.METADATA
    classifier: str = "metadata"
    injection_detected: bool = False
    injection_score: float = Field(default=0.0, ge=0.0, le=1.0)
    injection_signals: list[str] = Field(default_factory=list)
    used_llm: bool = False
    text_sha256: str | None = None


class RiskFactor(_Strict):
    name: str
    contribution: float
    description: str
    reason_code: ReasonCode | None = None


class RiskAssessment(_Strict):
    distraction_risk: float = Field(ge=0.0, le=1.0)
    doomscroll_probability: float = Field(ge=0.0, le=1.0)
    goal_conflict: float = Field(ge=0.0, le=1.0)
    intervention_urgency: float = Field(ge=0.0, le=1.0)
    continuation_probability: float = Field(ge=0.0, le=1.0)
    predicted_productivity_loss_minutes: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    method: RiskMethod
    model_version: str | None = None
    factors: list[RiskFactor] = Field(default_factory=list)

    @property
    def band(self) -> Literal["low", "mild", "elevated", "high"]:
        r = self.distraction_risk
        if r >= 0.75:
            return "high"
        if r >= 0.6:
            return "elevated"
        if r >= 0.45:
            return "mild"
        return "low"


class MemoryHit(_Strict):
    id: uuid.UUID
    memory_type: MemoryType
    content: str
    importance: float
    similarity: float
    score: float
    created_at: datetime
    is_recent: bool
    meta: dict[str, Any] = Field(default_factory=dict)


class DecisionProposal(_Strict):
    """Output schema of the Decision Agent (section 44). Validated before anything executes."""

    decision: InterventionType
    duration_minutes: int = Field(default=0, ge=0, le=240)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=8)
    user_visible_explanation: str = Field(default="", max_length=400)


class DecisionRecord(_Strict):
    proposal: DecisionProposal
    source: DecisionSource
    eligible: list[InterventionType]
    prior: InterventionType
    bandit_scores: dict[str, float] | None = None
    bandit_context: list[float] | None = None
    llm_used: bool = False
    notes: list[str] = Field(default_factory=list)


class AuthorizationResult(_Strict):
    proposed: InterventionType
    decision: InterventionType
    duration_minutes: int = Field(ge=0, le=120)
    authorized_unchanged: bool
    flags: list[ReasonCode] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    consumed_override_id: uuid.UUID | None = None
    suppressed_duplicate_of: uuid.UUID | None = None
    ticket: str | None = None


CommandName = Literal[
    "NONE", "NOTIFY", "MINDFUL_PROMPT", "DELAY_GATE", "LIMIT_ACCESS", "BLOCK_APP", "START_FOCUS", "CONFIRM"
]
CommandAction = Literal["ACCEPT", "ALLOW_ONCE", "EMERGENCY", "CONTINUE", "STOP", "START_FOCUS"]


class DeviceCommand(_Strict):
    command: CommandName
    intervention_id: uuid.UUID | None = None
    app_package: str | None = None
    duration_seconds: int = Field(default=0, ge=0, le=7200)
    title: str = Field(default="", max_length=80)
    body: str = Field(default="", max_length=400)
    actions: list[CommandAction] = Field(default_factory=list)
    expires_at: datetime | None = None
    reversible: bool = True


class ActionResult(_Strict):
    intervention_id: uuid.UUID | None = None
    command: DeviceCommand
    explanation: str
    explanation_points: list[str] = Field(default_factory=list)
    created: bool = False


class OutcomeRecord(_Strict):
    intervention_id: uuid.UUID
    outcome: OutcomeType
    reward: float = Field(ge=-1.0, le=1.0)
    satisfaction: int | None = Field(default=None, ge=1, le=5)
