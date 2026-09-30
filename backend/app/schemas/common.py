"""Shared enumerations. These are the single source of truth for every
closed vocabulary in the system (DB check constraints, API validation,
LLM output schemas and the Android client all mirror these values)."""

from __future__ import annotations

from enum import StrEnum


class InterventionType(StrEnum):
    ALLOW = "ALLOW"
    SOFT_WARNING = "SOFT_WARNING"
    MINDFUL_PROMPT = "MINDFUL_PROMPT"
    DELAY = "DELAY"
    LIMITED_ACCESS = "LIMITED_ACCESS"
    TEMPORARY_BLOCK = "TEMPORARY_BLOCK"
    FOCUS_MODE = "FOCUS_MODE"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"


HARD_INTERVENTIONS: frozenset[InterventionType] = frozenset(
    {InterventionType.LIMITED_ACCESS, InterventionType.TEMPORARY_BLOCK, InterventionType.FOCUS_MODE}
)

# Ordinal strength used by the guardrails ("never escalate beyond ...").
INTERVENTION_SEVERITY: dict[InterventionType, int] = {
    InterventionType.ALLOW: 0,
    InterventionType.SOFT_WARNING: 1,
    InterventionType.MINDFUL_PROMPT: 1,
    InterventionType.REQUEST_CONFIRMATION: 2,
    InterventionType.DELAY: 2,
    InterventionType.LIMITED_ACCESS: 3,
    InterventionType.FOCUS_MODE: 3,
    InterventionType.TEMPORARY_BLOCK: 4,
}

# Hard caps enforced by the guardrail engine regardless of what any agent proposes.
MAX_DURATION_MINUTES: dict[InterventionType, int] = {
    InterventionType.ALLOW: 0,
    InterventionType.SOFT_WARNING: 0,
    InterventionType.MINDFUL_PROMPT: 0,
    InterventionType.REQUEST_CONFIRMATION: 0,
    InterventionType.DELAY: 5,
    InterventionType.LIMITED_ACCESS: 30,
    InterventionType.TEMPORARY_BLOCK: 60,
    InterventionType.FOCUS_MODE: 120,
}

DEFAULT_DURATION_MINUTES: dict[InterventionType, int] = {
    InterventionType.DELAY: 2,
    InterventionType.LIMITED_ACCESS: 10,
    InterventionType.TEMPORARY_BLOCK: 15,
    InterventionType.FOCUS_MODE: 25,
}


class ContentCategory(StrEnum):
    EDUCATION = "education"
    CAREER = "career"
    TECHNOLOGY = "technology"
    NEWS = "news"
    ENTERTAINMENT = "entertainment"
    ADVERTISING = "advertising"
    GAMING = "gaming"
    SPORTS = "sports"
    SHORT_FORM_VIDEO = "short_form_video"
    CLICKBAIT = "clickbait"
    SOCIAL = "social"
    PRODUCTIVITY = "productivity"
    COMMUNICATION = "communication"
    UNKNOWN = "unknown"


ENTERTAINMENT_LIKE: frozenset[ContentCategory] = frozenset(
    {ContentCategory.ENTERTAINMENT, ContentCategory.SHORT_FORM_VIDEO, ContentCategory.GAMING, ContentCategory.CLICKBAIT}
)
PRODUCTIVE: frozenset[ContentCategory] = frozenset(
    {ContentCategory.EDUCATION, ContentCategory.CAREER, ContentCategory.TECHNOLOGY, ContentCategory.PRODUCTIVITY}
)


class GoalRelevance(StrEnum):
    RELEVANT = "goal_relevant"
    IRRELEVANT = "goal_irrelevant"
    NEUTRAL = "neutral"


class AppCategory(StrEnum):
    SOCIAL_MEDIA = "social_media"
    VIDEO = "video"
    MESSAGING = "messaging"
    BROWSER = "browser"
    GAMES = "games"
    PRODUCTIVITY = "productivity"
    EDUCATION = "education"
    NEWS = "news"
    ESSENTIAL = "essential"
    OTHER = "other"


class EventType(StrEnum):
    APP_OPENED = "APP_OPENED"
    APP_CLOSED = "APP_CLOSED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_EXTENDED = "SESSION_EXTENDED"
    FOCUS_STARTED = "FOCUS_STARTED"
    FOCUS_ENDED = "FOCUS_ENDED"
    RISK_DETECTED = "RISK_DETECTED"
    INTERVENTION_TRIGGERED = "INTERVENTION_TRIGGERED"
    INTERVENTION_ACCEPTED = "INTERVENTION_ACCEPTED"
    INTERVENTION_OVERRIDDEN = "INTERVENTION_OVERRIDDEN"
    GOAL_UPDATED = "GOAL_UPDATED"
    POLICY_UPDATED = "POLICY_UPDATED"
    GUARDIAN_PAUSED = "GUARDIAN_PAUSED"
    GUARDIAN_RESUMED = "GUARDIAN_RESUMED"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"
    CONSENT_CHANGED = "CONSENT_CHANGED"
    USAGE_SUMMARY = "USAGE_SUMMARY"
    DATA_DELETED = "DATA_DELETED"


# Only these may be submitted by a device. Everything else is emitted by the server itself,
# so a client cannot forge e.g. POLICY_UPDATED or INTERVENTION_TRIGGERED.
CLIENT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.APP_OPENED,
        EventType.APP_CLOSED,
        EventType.SESSION_STARTED,
        EventType.SESSION_EXTENDED,
        EventType.FOCUS_STARTED,
        EventType.FOCUS_ENDED,
        EventType.USAGE_SUMMARY,
    }
)


class ConsentScope(StrEnum):
    USAGE_MONITORING = "usage_monitoring"
    CONTENT_TEXT_ANALYSIS = "content_text_analysis"
    CLOUD_AI_REASONING = "cloud_ai_reasoning"
    SCREENSHOT_ANALYSIS = "screenshot_analysis"
    TRANSCRIPT_ANALYSIS = "transcript_analysis"
    MEMORY_PERSONALIZATION = "memory_personalization"
    ANONYMIZED_RESEARCH = "anonymized_research"


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"


class InterventionStyle(StrEnum):
    GENTLE = "gentle"
    BALANCED = "balanced"
    STRICT = "strict"


STYLE_MAX_SEVERITY: dict[InterventionStyle, int] = {
    InterventionStyle.GENTLE: 2,
    InterventionStyle.BALANCED: 3,
    InterventionStyle.STRICT: 4,
}


class InterventionStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"
    IGNORED = "ignored"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OutcomeType(StrEnum):
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"
    IGNORED = "ignored"
    STOPPED_SESSION = "stopped_session"
    CONTINUED_SESSION = "continued_session"
    RETURNED_TO_TASK = "returned_to_task"
    DISABLED_PROTECTION = "disabled_protection"


POSITIVE_OUTCOMES: frozenset[OutcomeType] = frozenset(
    {OutcomeType.ACCEPTED, OutcomeType.STOPPED_SESSION, OutcomeType.RETURNED_TO_TASK}
)


class OverrideKind(StrEnum):
    ALLOW_ONCE = "allow_once"
    PAUSE = "pause"
    DISABLE_30M = "disable_30m"
    DISABLE_UNTIL_TOMORROW = "disable_until_tomorrow"
    EMERGENCY = "emergency"


class PolicyEffect(StrEnum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    DELAY = "DELAY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    LIMIT = "LIMIT"
    BLOCK = "BLOCK"


class ReasonCode(StrEnum):
    FOCUS_SESSION_ACTIVE = "FOCUS_SESSION_ACTIVE"
    FOCUS_WINDOW_ACTIVE = "FOCUS_WINDOW_ACTIVE"
    HIGH_RISK_SESSION = "HIGH_RISK_SESSION"
    ELEVATED_RISK_SESSION = "ELEVATED_RISK_SESSION"
    MILD_RISK_SESSION = "MILD_RISK_SESSION"
    LOW_RISK_SESSION = "LOW_RISK_SESSION"
    LONG_SESSION = "LONG_SESSION"
    ENTERTAINMENT_CONTENT = "ENTERTAINMENT_CONTENT"
    GOAL_RELEVANT_CONTENT = "GOAL_RELEVANT_CONTENT"
    POLICY_RULE_MATCHED = "POLICY_RULE_MATCHED"
    POLICY_EXCEPTION_ALLOWS = "POLICY_EXCEPTION_ALLOWS"
    DAILY_LIMIT_EXCEEDED = "DAILY_LIMIT_EXCEEDED"
    SESSION_LIMIT_EXCEEDED = "SESSION_LIMIT_EXCEEDED"
    TRIGGER_HOUR = "TRIGGER_HOUR"
    NIGHT_USAGE = "NIGHT_USAGE"
    REPEATED_OPENS = "REPEATED_OPENS"
    PERSONALIZED_CHOICE = "PERSONALIZED_CHOICE"
    MEMORY_PATTERN = "MEMORY_PATTERN"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"
    GUARDIAN_PAUSED = "GUARDIAN_PAUSED"
    ALLOW_ONCE = "ALLOW_ONCE"
    COOLDOWN = "COOLDOWN"
    RATE_LIMITED = "RATE_LIMITED"
    DURATION_CAPPED = "DURATION_CAPPED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    ESSENTIAL_APP_PROTECTED = "ESSENTIAL_APP_PROTECTED"
    UNTRUSTED_CONTENT_QUARANTINED = "UNTRUSTED_CONTENT_QUARANTINED"
    LLM_FALLBACK = "LLM_FALLBACK"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    BLOCK_BUDGET_EXHAUSTED = "BLOCK_BUDGET_EXHAUSTED"
    STYLE_CEILING = "STYLE_CEILING"
    POLICY_CEILING = "POLICY_CEILING"
    CONSENT_MISSING = "CONSENT_MISSING"
    CONTENT_UNCERTAIN = "CONTENT_UNCERTAIN"
    GUARDIAN_DISABLED = "GUARDIAN_DISABLED"
    DOOMSCROLL_PATTERN = "DOOMSCROLL_PATTERN"
    GOAL_CONFLICT = "GOAL_CONFLICT"


class DecisionSource(StrEnum):
    DETERMINISTIC = "deterministic"
    BANDIT = "bandit"
    LLM = "llm"
    FALLBACK = "fallback"
    OVERRIDE = "override"


class RunType(StrEnum):
    EVALUATE = "evaluate"
    FEEDBACK = "feedback"
    CONSTITUTION = "constitution"
    CONTENT_ANALYSIS = "content_analysis"
    REPLAY = "replay"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class ToolCallStatus(StrEnum):
    OK = "ok"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    DENIED = "denied"
    ERROR = "error"


class LLMCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    CACHED = "cached"


class MemoryType(StrEnum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PREFERENCE = "preference"
    INSIGHT = "insight"


class MemorySource(StrEnum):
    USER = "user"
    SYSTEM = "system"
    LEARNING = "learning"


class ContentSource(StrEnum):
    METADATA = "metadata"
    TEXT = "text"
    SCREENSHOT = "screenshot"
    TRANSCRIPT = "transcript"


class RiskMethod(StrEnum):
    RULES = "rules"
    ML = "ml"
    HYBRID = "hybrid"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class PolicyStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class CompiledBy(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    MANUAL = "manual"


class ModelKind(StrEnum):
    RISK = "risk"
    CONTENT = "content"
    BANDIT = "bandit"
    EMBEDDING = "embedding"
    ON_DEVICE = "on_device"
