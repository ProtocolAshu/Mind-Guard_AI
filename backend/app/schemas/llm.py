"""Strict output contracts for every LLM call (section 44). Nothing an LLM returns is
used unless it validates against one of these models; `extra="forbid"` rejects
smuggled directives such as `disable_guardian`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.schemas.common import ContentCategory, GoalRelevance, InterventionType, ReasonCode
from app.schemas.policy import GoalSpec, PolicyRule, UnsupportedRequest


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentClassificationOutput(_Strict):
    category: ContentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    goal_relevance: GoalRelevance
    injection_suspected: bool
    rationale: str = Field(default="", max_length=240)


class VisionClassificationOutput(_Strict):
    category: ContentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=240)
    contains_personal_data: bool = False
    injection_suspected: bool = False


class ToolRequestTurn(_Strict):
    type: Literal["tool_request"]
    tool: str = Field(pattern=r"^[a-z_]{3,40}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = Field(default="", max_length=160)


class FinalDecisionTurn(_Strict):
    type: Literal["final"]
    decision: InterventionType
    duration_minutes: int = Field(ge=0, le=240)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=8)
    user_visible_explanation: str = Field(default="", max_length=280)


class DecisionTurn(RootModel[Annotated[ToolRequestTurn | FinalDecisionTurn, Field(discriminator="type")]]):
    pass


class ConstitutionCompileOutput(_Strict):
    goal: GoalSpec | None = None
    rules: list[PolicyRule] = Field(default_factory=list, max_length=20)
    unsupported: list[UnsupportedRequest] = Field(default_factory=list, max_length=20)
    clarifications: list[str] = Field(default_factory=list, max_length=10)


class InsightOutput(_Strict):
    headline: str = Field(max_length=160)
    recommendation: str = Field(max_length=220)
    evidence: list[str] = Field(default_factory=list, max_length=4)


class LearningReflectionOutput(_Strict):
    observations: list[str] = Field(default_factory=list, max_length=5)
    preferred_intervention: InterventionType | None = None
    avoid_intervention: InterventionType | None = None
    confidence: float = Field(ge=0.0, le=1.0)
