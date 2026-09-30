"""API request/response models (OpenAPI documented)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.agents import DeviceCapabilities
from app.schemas.common import (
    CompiledBy,
    ConsentScope,
    ContentCategory,
    GoalStatus,
    InterventionStyle,
    OverrideKind,
    PolicyStatus,
)
from app.schemas.policy import PACKAGE_PATTERN, PolicyRule, ValidationReport

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown IANA timezone") from exc
    return value


class RegisterRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=80)
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v

    _validate_tz = field_validator("timezone")(classmethod(lambda cls, v: _tz(v)))


class LoginRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(Strict):
    refresh_token: str = Field(min_length=20, max_length=200)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    refresh_expires_in: int
    user: UserOut


class UserUpdate(Strict):
    display_name: str | None = Field(default=None, max_length=80)


class PasswordChange(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PreferencesOut(ORM):
    intervention_style: InterventionStyle
    timezone: str
    guardian_enabled: bool
    ai_analysis_enabled: bool
    content_analysis_enabled: bool
    daily_social_limit_minutes: int | None
    retention_days: int
    theme: str
    device_capabilities: dict[str, Any]


class PreferencesUpdate(Strict):
    intervention_style: InterventionStyle | None = None
    timezone: str | None = Field(default=None, max_length=64)
    guardian_enabled: bool | None = None
    ai_analysis_enabled: bool | None = None
    content_analysis_enabled: bool | None = None
    daily_social_limit_minutes: int | None = Field(default=None, ge=15, le=1440)
    retention_days: int | None = Field(default=None, ge=7, le=365)
    theme: Literal["system", "light", "dark"] | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str | None) -> str | None:
        return _tz(v) if v is not None else v


CONSENT_DESCRIPTIONS: dict[ConsentScope, str] = {
    ConsentScope.USAGE_MONITORING: "App usage time and session events from Usage Access. Required for any intervention.",
    ConsentScope.CONTENT_TEXT_ANALYSIS: "Classify captions or titles you share, locally first. Raw text is never stored.",
    ConsentScope.CLOUD_AI_REASONING: "Allow a cloud language model for ambiguous cases, within your token budget.",
    ConsentScope.SCREENSHOT_ANALYSIS: "Analyse screenshots you share manually. Images are processed and discarded.",
    ConsentScope.TRANSCRIPT_ANALYSIS: "Classify video transcripts you share.",
    ConsentScope.MEMORY_PERSONALIZATION: "Remember intervention outcomes to personalise future decisions.",
    ConsentScope.ANONYMIZED_RESEARCH: "Include anonymised aggregate statistics in research evaluation.",
}


class ConsentOut(BaseModel):
    scope: ConsentScope
    granted: bool
    description: str
    updated_at: datetime | None = None


class ConsentUpdate(Strict):
    granted: bool


class SettingsOut(BaseModel):
    preferences: PreferencesOut
    consents: list[ConsentOut]


class GoalCreate(Strict):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    priority: int = Field(default=3, ge=1, le=5)
    is_temporary: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def _dates(self) -> GoalCreate:
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("dates must include a timezone")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class GoalUpdate(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    priority: int | None = Field(default=None, ge=1, le=5)
    status: GoalStatus | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class GoalOut(ORM):
    id: uuid.UUID
    title: str
    description: str
    status: GoalStatus
    priority: int
    is_temporary: bool
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    relevant_categories: list[ContentCategory] = Field(default_factory=list)


class PolicyFromConstitution(Strict):
    text: str = Field(min_length=3, max_length=5000)
    allow_llm: bool = True
    name: str = Field(default="Personal AI Constitution", min_length=1, max_length=120)


class PolicyRulesUpdate(Strict):
    rules: list[PolicyRule] = Field(max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_text: str = Field(default="", max_length=5000)


class ValidateRulesRequest(Strict):
    rules: list[PolicyRule] = Field(max_length=50)


class PolicyStatusUpdate(Strict):
    status: PolicyStatus


class PolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    status: PolicyStatus
    goal_id: uuid.UUID | None
    current_version: int
    compiled_by: CompiledBy
    source_text: str
    rules: list[PolicyRule]
    rule_explanations: dict[str, str]
    validation: ValidationReport
    created_at: datetime
    updated_at: datetime


class PolicyVersionOut(BaseModel):
    version: int
    compiled_by: CompiledBy
    rule_count: int
    source_text: str
    created_at: datetime


class OverrideCreate(Strict):
    kind: OverrideKind
    app_package: str | None = Field(default=None, pattern=PACKAGE_PATTERN, max_length=255)
    reason: str | None = Field(default=None, max_length=280)


class OverrideOut(ORM):
    id: uuid.UUID
    kind: OverrideKind
    app_package: str | None
    reason: str | None
    starts_at: datetime
    expires_at: datetime | None
    consumed_at: datetime | None
    revoked_at: datetime | None


class EventBatch(Strict):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class RejectedEvent(BaseModel):
    index: int
    client_event_id: str | None
    reason: str


class EventBatchResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[RejectedEvent]


class MemoryCreate(Strict):
    content: str = Field(min_length=1, max_length=1000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)


class MemorySearch(Strict):
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(default=5, ge=1, le=20)


class MemoryOut(ORM):
    id: uuid.UUID
    memory_type: str
    content: str
    importance: float
    source: str
    meta: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None


class DeviceUpdate(Strict):
    capabilities: DeviceCapabilities


class RiskAssessRequest(Strict):
    app_package: str = Field(pattern=PACKAGE_PATTERN, max_length=255)
    session_minutes: float | None = Field(default=None, ge=0, le=1440)
