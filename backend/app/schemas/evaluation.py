"""Request/response contracts for agent runs exposed through the API."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agents import ContentAssessment, DeviceCapabilities, DeviceCommand, RiskAssessment, RiskFactor
from app.schemas.common import (
    CompiledBy,
    ContentSource,
    DecisionSource,
    InterventionType,
    OutcomeType,
    PolicyEffect,
    ReasonCode,
    RiskMethod,
)
from app.schemas.policy import PACKAGE_PATTERN, CompiledConstitution, ValidationReport


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentInput(_Strict):
    text: str = Field(min_length=1, max_length=40_000)
    source: ContentSource = ContentSource.TEXT


class EvaluateRequest(_Strict):
    app_package: str = Field(pattern=PACKAGE_PATTERN, max_length=255)
    session_minutes: float | None = Field(default=None, ge=0, le=1440)
    content: ContentInput | None = None
    device_capabilities: DeviceCapabilities | None = None


class RiskSummary(BaseModel):
    distraction_risk: float
    doomscroll_probability: float
    goal_conflict: float
    intervention_urgency: float
    continuation_probability: float
    predicted_productivity_loss_minutes: float
    confidence: float
    method: RiskMethod
    band: str
    model_version: str | None = None
    factors: list[RiskFactor] = Field(default_factory=list)

    @classmethod
    def of(cls, risk: RiskAssessment) -> RiskSummary:
        return cls(**risk.model_dump(exclude={"factors"}), band=risk.band, factors=risk.factors[:4])


class PolicySummary(BaseModel):
    effect: PolicyEffect | None = None
    winning_rule_id: str | None = None
    rule_description: str | None = None
    explanation: str = ""
    in_restricted_window: bool = False


class EvaluationResponse(BaseModel):
    run_id: uuid.UUID
    decision: InterventionType
    proposed: InterventionType
    duration_minutes: int
    confidence: float
    decided_by: DecisionSource
    reason_codes: list[ReasonCode]
    guardrail_flags: list[ReasonCode]
    explanation: str
    explanation_points: list[str]
    command: DeviceCommand
    intervention_id: uuid.UUID | None = None
    risk: RiskSummary | None = None
    policy: PolicySummary | None = None
    content: ContentAssessment | None = None
    degraded: bool = False
    path: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FeedbackRequest(_Strict):
    intervention_id: uuid.UUID
    outcome: OutcomeType
    satisfaction: int | None = Field(default=None, ge=1, le=5)


class FeedbackResponse(BaseModel):
    run_id: uuid.UUID
    intervention_id: uuid.UUID
    outcome: OutcomeType
    reward: float
    decision: InterventionType
    bandit_updated: bool
    memory_written: bool
    preference: dict[str, Any] | None = None
    suggestion: dict[str, Any] | None = None
    insights: list[str] = Field(default_factory=list)


class ConstitutionRequest(_Strict):
    text: str = Field(min_length=3, max_length=5000)
    allow_llm: bool = True


class ConstitutionPreviewResponse(BaseModel):
    run_id: uuid.UUID
    constitution: CompiledConstitution
    compiled_by: CompiledBy
    validation: ValidationReport
    parsed_sentences: list[str]
    unparsed_sentences: list[str]
    summary: dict[str, Any]
    llm_used: bool
    grounding: list[str]
