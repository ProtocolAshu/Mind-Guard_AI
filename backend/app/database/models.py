"""ORM models — the normalized schema from section 20 of the master prompt.

Design notes
* Every user-owned table carries `user_id` with ON DELETE CASCADE so account
  deletion is complete and cross-user queries are always filterable.
* Closed vocabularies are CHECK constraints generated from `app.schemas.common`.
* Raw social-media content is never stored: `content_signals` keeps only the
  category, confidence and a SHA-256 fingerprint.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, EmbeddingVector, JSONType, UTCDateTime, enum_check, utcnow
from app.schemas.common import (
    AppCategory,
    CompiledBy,
    ConsentScope,
    ContentCategory,
    ContentSource,
    DecisionSource,
    EventType,
    GoalRelevance,
    GoalStatus,
    InterventionStatus,
    InterventionStyle,
    InterventionType,
    LLMCallStatus,
    MemorySource,
    MemoryType,
    ModelKind,
    OutcomeType,
    OverrideKind,
    PolicyStatus,
    RiskMethod,
    Role,
    RunStatus,
    RunType,
    ToolCallStatus,
)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _user_fk(nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[Any]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete=ondelete), nullable=nullable, index=not nullable)


def _created() -> Mapped[datetime]:
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow)


def _prob_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0 AND {column} <= 1", name=f"{column}_range")


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=Role.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (enum_check("role", Role),)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    intervention_style: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    guardian_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ai_analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    content_analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_social_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    device_capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        enum_check("intervention_style", InterventionStyle),
        CheckConstraint("retention_days >= 1 AND retention_days <= 3650", name="retention_days_range"),
        CheckConstraint(
            "daily_social_limit_minutes IS NULL OR (daily_social_limit_minutes >= 5 AND daily_social_limit_minutes <= 1440)",
            name="daily_limit_range",
        ),
        CheckConstraint("theme IN ('system', 'light', 'dark')", name="theme_valid"),
    )


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False, default="2026-09")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "scope"), enum_check("scope", ConsentScope))


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=GoalStatus.ACTIVE.value)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    is_temporary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        enum_check("status", GoalStatus),
        CheckConstraint("priority >= 1 AND priority <= 5", name="priority_range"),
    )


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PolicyStatus.ACTIVE.value)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (enum_check("status", PolicyStatus),)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[uuid.UUID] = _pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = _user_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compiled_by: Mapped[str] = mapped_column(String(16), nullable=False, default=CompiledBy.DETERMINISTIC.value)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("policy_id", "version"), enum_check("compiled_by", CompiledBy))


class PolicyOverride(Base):
    __tablename__ = "policy_overrides"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    app_package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(280), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (enum_check("kind", OverrideKind),)


class App(Base):
    __tablename__ = "apps"
    id: Mapped[uuid.UUID] = _pk()
    package_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default=AppCategory.OTHER.value)
    is_social: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (enum_check("category", AppCategory),)


class UsageSession(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    app_package: Mapped[str] = mapped_column(String(255), nullable=False)
    app_category: Mapped[str] = mapped_column(String(32), nullable=False, default=AppCategory.OTHER.value)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scroll_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    focus_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        enum_check("app_category", AppCategory),
        enum_check("content_category", ContentCategory),
        CheckConstraint("duration_seconds >= 0", name="duration_non_negative"),
        Index("ix_sessions_user_started", "user_id", "started_at"),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    client_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    app_package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("user_id", "client_event_id"),
        enum_check("event_type", EventType),
        Index("ix_events_user_occurred", "user_id", "occurred_at"),
    )


class ContentSignal(Base):
    __tablename__ = "content_signals"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    goal_relevance: Mapped[str] = mapped_column(String(24), nullable=False, default=GoalRelevance.NEUTRAL.value)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    classifier: Mapped[str] = mapped_column(String(64), nullable=False)
    injection_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    injection_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        enum_check("source", ContentSource),
        enum_check("category", ContentCategory),
        enum_check("goal_relevance", GoalRelevance),
        _prob_check("confidence"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING.value)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    path: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    final_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    __table_args__ = (
        enum_check("run_type", RunType),
        enum_check("status", RunStatus),
        Index("ix_agent_runs_started", "started_at"),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        CheckConstraint("status IN ('ok', 'skipped', 'error', 'fallback')", name="status_valid"),
    )


class RiskScore(Base):
    __tablename__ = "risk_scores"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    distraction_risk: Mapped[float] = mapped_column(Float, nullable=False)
    doomscroll_probability: Mapped[float] = mapped_column(Float, nullable=False)
    goal_conflict: Mapped[float] = mapped_column(Float, nullable=False)
    intervention_urgency: Mapped[float] = mapped_column(Float, nullable=False)
    continuation_probability: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_productivity_loss_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    factors: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)  # one version per target, comma-joined
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        enum_check("method", RiskMethod),
        _prob_check("distraction_risk"),
        _prob_check("doomscroll_probability"),
        _prob_check("goal_conflict"),
        _prob_check("intervention_urgency"),
        _prob_check("continuation_probability"),
        _prob_check("confidence"),
        Index("ix_risk_scores_user_created", "user_id", "created_at"),
    )


class Intervention(Base):
    __tablename__ = "interventions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    app_package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proposed_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    final_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    guardrail_flags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=InterventionStatus.DELIVERED.value)
    bandit_context: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    command: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (
        enum_check("proposed_decision", InterventionType),
        enum_check("final_decision", InterventionType),
        enum_check("decided_by", DecisionSource),
        enum_check("status", InterventionStatus),
        _prob_check("confidence"),
        CheckConstraint("duration_minutes >= 0 AND duration_minutes <= 120", name="duration_range"),
        Index("ix_interventions_user_created", "user_id", "created_at"),
    )


class InterventionOutcome(Base):
    __tablename__ = "intervention_outcomes"
    id: Mapped[uuid.UUID] = _pk()
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("interventions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user_id: Mapped[uuid.UUID] = _user_fk()
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    satisfaction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        enum_check("outcome", OutcomeType),
        CheckConstraint("reward >= -1 AND reward <= 1", name="reward_range"),
        CheckConstraint("satisfaction IS NULL OR (satisfaction >= 1 AND satisfaction <= 5)", name="satisfaction_range"),
    )


class BehaviorFeature(Base):
    __tablename__ = "behavior_features"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    feature_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_social_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_screen_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    productive_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sessions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    long_sessions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_session_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opens_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    focus_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    night_social_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interventions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overridden_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hourly_social_seconds: Mapped[list[int]] = mapped_column(JSONType, nullable=False, default=list)
    app_seconds: Mapped[dict[str, int]] = mapped_column(JSONType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "feature_date"),)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=MemorySource.SYSTEM.value)
    created_at: Mapped[datetime] = _created()
    last_accessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (
        enum_check("memory_type", MemoryType),
        enum_check("source", MemorySource),
        _prob_check("importance"),
        Index("ix_memories_user_type", "user_id", "memory_type"),
    )


class Embedding(Base):
    __tablename__ = "embeddings"
    id: Mapped[uuid.UUID] = _pk()
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model"),
        CheckConstraint("owner_type IN ('memory', 'knowledge')", name="owner_type_valid"),
        CheckConstraint("dim > 0 AND dim <= 4096", name="dim_range"),
        Index(
            "ix_embeddings_hnsw_384",
            text("(embedding::vector(384)) vector_cosine_ops"),
            postgresql_using="hnsw",
            postgresql_where=text("dim = 384"),
        ).ddl_if(dialect="postgresql"),
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("slug", "chunk_index"),)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[uuid.UUID] = _pk()
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trained_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("model_name", "version"), enum_check("kind", ModelKind))


class BanditState(Base):
    __tablename__ = "bandit_states"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    n_features: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    total_updates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (enum_check("status", ToolCallStatus),)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        enum_check("status", LLMCallStatus),
        Index("ix_llm_calls_created", "created_at"),
        Index("ix_llm_calls_user_created", "user_id", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = _created()


USER_OWNED_TABLES = [
    "refresh_tokens", "consents", "goals", "policy_versions", "policies", "policy_overrides",
    "intervention_outcomes", "interventions", "risk_scores", "content_signals", "events", "sessions",
    "behavior_features", "memories", "embeddings", "bandit_states", "tool_calls", "llm_calls", "agent_runs",
]

# Referenced by tests to make sure every value set is covered by a constraint.
_ENUM_TABLES: dict[str, list[type]] = {
    "interventions": [InterventionType, DecisionSource, InterventionStatus],
    "content_signals": [ContentSource, ContentCategory, GoalRelevance],
    "llm_calls": [LLMCallStatus],
    "tool_calls": [ToolCallStatus],
    "agent_runs": [RunType, RunStatus],
    "memories": [MemoryType, MemorySource],
    "intervention_outcomes": [OutcomeType],
    "model_versions": [ModelKind],
    "risk_scores": [RiskMethod],
}
