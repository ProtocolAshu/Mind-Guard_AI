"""Risk feature pipeline (section 8) — one definition shared by online scoring,
offline training (ml/) and the simulator, so train/serve skew cannot creep in."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.schemas.common import ENTERTAINMENT_LIKE, PRODUCTIVE, AppCategory, ContentCategory

RISK_FEATURES: tuple[str, ...] = (
    "session_minutes",
    "hour_sin",
    "hour_cos",
    "is_night",
    "is_weekend",
    "focus_active",
    "in_restricted_window",
    "content_entertainment",
    "content_productive",
    "content_confidence",
    "app_social",
    "app_video",
    "opens_last_hour",
    "daily_social_minutes",
    "daily_limit_ratio",
    "trigger_hour",
    "avg_session_minutes_14d",
    "long_session_rate_14d",
    "override_rate_14d",
    "accept_rate_14d",
    "minutes_since_last_intervention",
    "scroll_rate",
    "previous_session_minutes",
)
DEFAULT_DAILY_REFERENCE_MINUTES = 120.0
LONG_SESSION_MINUTES = 30


@dataclass(frozen=True)
class RiskInput:
    session_minutes: float
    hour: int
    minute: int = 0
    weekday: int = 0
    focus_active: bool = False
    in_restricted_window: bool = False
    content_category: ContentCategory = ContentCategory.UNKNOWN
    content_confidence: float = 0.0
    goal_relevant: bool = False
    app_category: AppCategory = AppCategory.OTHER
    opens_last_hour: int = 0
    daily_social_minutes: float = 0.0
    daily_limit_minutes: int | None = None
    trigger_hours: tuple[int, ...] = ()
    avg_session_minutes_14d: float = 0.0
    long_session_rate_14d: float = 0.0
    override_rate_14d: float = 0.0
    accept_rate_14d: float = 0.0
    minutes_since_last_intervention: float | None = None
    scroll_events: int = 0
    previous_session_minutes: float = 0.0
    history_days: int = 0


def is_night_hour(hour: int) -> bool:
    return hour >= 22 or hour < 5


def featurize(inp: RiskInput) -> dict[str, float]:
    angle = 2 * math.pi * ((inp.hour % 24) + inp.minute / 60.0) / 24.0
    limit = float(inp.daily_limit_minutes) if inp.daily_limit_minutes else DEFAULT_DAILY_REFERENCE_MINUTES
    since = inp.minutes_since_last_intervention
    return {
        "session_minutes": max(0.0, float(inp.session_minutes)),
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "is_night": float(is_night_hour(inp.hour)),
        "is_weekend": float(inp.weekday >= 5),
        "focus_active": float(inp.focus_active),
        "in_restricted_window": float(inp.in_restricted_window),
        "content_entertainment": float(inp.content_category in ENTERTAINMENT_LIKE),
        "content_productive": float(inp.content_category in PRODUCTIVE or inp.goal_relevant),
        "content_confidence": min(1.0, max(0.0, inp.content_confidence)),
        "app_social": float(inp.app_category is AppCategory.SOCIAL_MEDIA),
        "app_video": float(inp.app_category is AppCategory.VIDEO),
        "opens_last_hour": float(max(0, inp.opens_last_hour)),
        "daily_social_minutes": max(0.0, inp.daily_social_minutes),
        "daily_limit_ratio": max(0.0, inp.daily_social_minutes) / limit,
        "trigger_hour": float(inp.hour in inp.trigger_hours),
        "avg_session_minutes_14d": max(0.0, inp.avg_session_minutes_14d),
        "long_session_rate_14d": min(1.0, max(0.0, inp.long_session_rate_14d)),
        "override_rate_14d": min(1.0, max(0.0, inp.override_rate_14d)),
        "accept_rate_14d": min(1.0, max(0.0, inp.accept_rate_14d)),
        "minutes_since_last_intervention": 240.0 if since is None else min(240.0, max(0.0, since)),
        "scroll_rate": inp.scroll_events / max(1.0, inp.session_minutes),
        "previous_session_minutes": max(0.0, inp.previous_session_minutes),
    }


def to_vector(features: dict[str, float]) -> list[float]:
    return [float(features[name]) for name in RISK_FEATURES]
