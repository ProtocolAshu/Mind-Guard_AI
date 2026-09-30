"""Risk Agent core: interpretable logistic scoring + optional calibrated ML models.

Rules are always computed (they explain the decision). When verified model
artifacts are present, the final probability blends rules and ML:
`p = w * p_ml + (1 - w) * p_rules`. Artifacts are loaded only if their SHA-256
matches the registry entry (joblib files can execute code when unpickled).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.interfaces import BehaviorModel
from app.risk.features import RISK_FEATURES, RiskInput, featurize, to_vector
from app.schemas.agents import RiskAssessment, RiskFactor
from app.schemas.common import ReasonCode, RiskMethod

log = logging.getLogger(__name__)
TARGETS = ("distraction", "doomscroll", "continuation")

Term = tuple[str, float, Callable[[dict[str, float]], float], str, ReasonCode | None]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _conf(f: dict[str, float]) -> float:
    return max(f["content_confidence"], 0.5)


DISTRACTION_BIAS = -3.2
DISTRACTION_TERMS: tuple[Term, ...] = (
    ("session_length", 0.045, lambda f: min(f["session_minutes"], 90.0),
     "your current session has lasted {session_minutes:.0f} minutes", ReasonCode.LONG_SESSION),
    ("restricted_window", 1.3, lambda f: f["in_restricted_window"], "you are inside a window your policy protects",
     ReasonCode.FOCUS_WINDOW_ACTIVE),
    ("focus_session", 1.0, lambda f: f["focus_active"] * max(f["app_social"], f["app_video"]),
     "you are in a focus session", ReasonCode.FOCUS_SESSION_ACTIVE),
    ("night", 0.9, lambda f: f["is_night"], "it is late at night", ReasonCode.NIGHT_USAGE),
    ("entertainment", 1.1, lambda f: f["content_entertainment"] * _conf(f), "the content looks like entertainment",
     ReasonCode.ENTERTAINMENT_CONTENT),
    ("productive_content", -1.6, lambda f: f["content_productive"] * _conf(f), "the content looks goal-relevant",
     ReasonCode.GOAL_RELEVANT_CONTENT),
    ("repeated_opens", 0.22, lambda f: min(f["opens_last_hour"], 10.0),
     "you opened social apps {opens_last_hour:.0f} times in the last hour", ReasonCode.REPEATED_OPENS),
    ("trigger_hour", 0.7, lambda f: f["trigger_hour"], "this is one of your usual high-usage hours", ReasonCode.TRIGGER_HOUR),
    ("daily_limit", 0.9, lambda f: _clip(f["daily_limit_ratio"] - 0.8, 0.0, 1.5),
     "today's social-media time is near or over your limit", ReasonCode.DAILY_LIMIT_EXCEEDED),
    ("long_session_history", 1.2, lambda f: f["long_session_rate_14d"], "long sessions have been common for you recently",
     ReasonCode.DOOMSCROLL_PATTERN),
    ("video_app", 0.35, lambda f: f["app_video"], "video apps tend to run long", None),
    ("social_app", 0.25, lambda f: f["app_social"], "social-media feed", None),
    ("scroll_rate", 0.03, lambda f: min(f["scroll_rate"], 30.0), "you are scrolling quickly", None),
)
DOOMSCROLL_BIAS = -3.6
DOOMSCROLL_TERMS: tuple[Term, ...] = (
    ("session_length", 0.055, lambda f: min(f["session_minutes"], 90.0), "", None),
    ("night", 0.8, lambda f: f["is_night"], "", None),
    ("entertainment", 1.0, lambda f: f["content_entertainment"] * _conf(f), "", None),
    ("long_session_history", 1.8, lambda f: f["long_session_rate_14d"], "", None),
    ("avg_session", 0.04, lambda f: _clip(f["avg_session_minutes_14d"] - 10.0, 0.0, 40.0), "", None),
    ("scroll_rate", 0.05, lambda f: min(f["scroll_rate"], 40.0), "", None),
    ("trigger_hour", 0.5, lambda f: f["trigger_hour"], "", None),
    ("video_app", 0.6, lambda f: f["app_video"], "", None),
    ("productive_content", -1.2, lambda f: f["content_productive"] * _conf(f), "", None),
)
CONTINUATION_BIAS = -1.4
CONTINUATION_TERMS: tuple[Term, ...] = (
    ("session_length", 0.03, lambda f: min(f["session_minutes"], 90.0), "", None),
    ("long_session_history", 1.4, lambda f: f["long_session_rate_14d"], "", None),
    ("entertainment", 0.7, lambda f: f["content_entertainment"] * _conf(f), "", None),
    ("night", 0.5, lambda f: f["is_night"], "", None),
    ("accept_rate", -0.9, lambda f: f["accept_rate_14d"], "", None),
    ("override_rate", 0.8, lambda f: f["override_rate_14d"], "", None),
    ("recent_intervention", -0.6, lambda f: float(f["minutes_since_last_intervention"] < 15.0), "", None),
)


def _logit(bias: float, terms: tuple[Term, ...], f: dict[str, float]) -> tuple[float, list[tuple[Term, float]]]:
    total = bias
    parts: list[tuple[Term, float]] = []
    for term in terms:
        contribution = term[1] * term[2](f)
        total += contribution
        parts.append((term, contribution))
    return total, parts


@dataclass(frozen=True)
class RuleScores:
    distraction: float
    doomscroll: float
    continuation: float
    goal_conflict: float
    confidence: float
    factors: list[RiskFactor]


def score_rules(inp: RiskInput, features: dict[str, float] | None = None) -> RuleScores:
    f = features or featurize(inp)
    d_logit, d_parts = _logit(DISTRACTION_BIAS, DISTRACTION_TERMS, f)
    s_logit, _ = _logit(DOOMSCROLL_BIAS, DOOMSCROLL_TERMS, f)
    c_logit, _ = _logit(CONTINUATION_BIAS, CONTINUATION_TERMS, f)
    scoped = max(f["focus_active"], f["in_restricted_window"])
    goal_conflict = _clip(
        0.55 * f["in_restricted_window"]
        + 0.35 * f["focus_active"] * max(f["app_social"], f["app_video"])
        + 0.25 * f["content_entertainment"] * (1.0 if scoped else 0.4)
        - 0.6 * f["content_productive"] * _conf(f)
    )
    factors = [
        RiskFactor(
            name=term[0],
            contribution=round(contribution, 3),
            description=term[3].format(**f),
            reason_code=term[4],
        )
        for term, contribution in sorted(d_parts, key=lambda p: -abs(p[1]))
        if abs(contribution) >= 0.15 and term[3]
    ][:6]
    history = min(inp.history_days / 14.0, 1.0)
    content_known = inp.content_confidence if inp.content_category.value != "unknown" else 0.0
    confidence = _clip(0.5 + 0.25 * history + 0.15 * content_known + 0.1 * float(inp.session_minutes >= 5), 0.35, 0.95)
    return RuleScores(_sigmoid(d_logit), _sigmoid(s_logit), _sigmoid(c_logit), goal_conflict, confidence, factors)


@dataclass
class _LoadedModel:
    target: str
    version: str
    model: Any
    auc: float | None


class RiskModelRegistry:
    """Loads the active, integrity-checked risk models listed in `<model_dir>/registry.json`."""

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.models: dict[str, _LoadedModel] = {}
        self.load_errors: list[str] = []
        self._load()

    def _load(self) -> None:
        registry_path = self.model_dir / "registry.json"
        if not registry_path.exists():
            return
        try:
            registry = json.loads(registry_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.load_errors.append(f"registry unreadable: {exc}")
            return
        for target in TARGETS:
            entry = (registry.get("active") or {}).get(target)
            if not entry:
                continue
            path = (self.model_dir / entry["path"]).resolve()
            if self.model_dir.resolve() not in path.parents:
                self.load_errors.append(f"{target}: artifact path escapes model_dir")
                continue
            if list(entry.get("features", [])) != list(RISK_FEATURES):
                self.load_errors.append(f"{target}: feature list mismatch (retrain required)")
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                self.load_errors.append(f"{target}: {exc}")
                continue
            if digest != entry.get("sha256"):
                self.load_errors.append(f"{target}: sha256 mismatch, refusing to load")
                log.warning("risk model integrity check failed", extra={"target": target})
                continue
            import joblib

            self.models[target] = _LoadedModel(
                target=target,
                version=str(entry.get("version")),
                model=joblib.load(path),  # nosec B301 - integrity verified against registry sha256
                auc=(entry.get("metrics") or {}).get("roc_auc"),
            )

    @property
    def available(self) -> bool:
        return "distraction" in self.models

    def version_string(self) -> str | None:
        if not self.models:
            return None
        return ",".join(f"{t}:{m.version}" for t, m in sorted(self.models.items()))

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        import numpy as np

        vector = np.asarray([to_vector(features)], dtype=float)
        return {t: float(m.model.predict_proba(vector)[0, 1]) for t, m in self.models.items()}


class RiskScorer(BehaviorModel):
    name = "hybrid-logistic-gbm"

    def __init__(self, registry: RiskModelRegistry | None = None, ml_weight: float = 0.6):
        self.registry = registry
        self.ml_weight = ml_weight

    def describe(self) -> dict[str, Any]:
        r = self.registry
        return {"name": self.name, "ml_available": bool(r and r.available), "versions": r.version_string() if r else None,
                "load_errors": r.load_errors if r else [], "ml_weight": self.ml_weight}

    def score(self, inp: RiskInput, *, use_ml: bool = True) -> RiskAssessment:
        features = featurize(inp)
        rules = score_rules(inp, features)
        distraction, doomscroll, continuation = rules.distraction, rules.doomscroll, rules.continuation
        confidence = rules.confidence
        method = RiskMethod.RULES
        version = None
        if use_ml and self.registry is not None and self.registry.available:
            try:
                ml = self.registry.predict(features)
            except Exception:  # model failure must never break scoring (failsafe)
                log.exception("risk model prediction failed; using rules only")
                ml = {}
            if "distraction" in ml:
                w = self.ml_weight
                distraction = w * ml["distraction"] + (1 - w) * rules.distraction
                doomscroll = w * ml.get("doomscroll", rules.doomscroll) + (1 - w) * rules.doomscroll
                continuation = w * ml.get("continuation", rules.continuation) + (1 - w) * rules.continuation
                agreement = 1.0 - abs(ml["distraction"] - rules.distraction)
                auc = self.registry.models["distraction"].auc or 0.75
                quality = _clip((auc - 0.5) * 2.0)
                confidence = _clip(0.5 * rules.confidence + 0.3 * agreement + 0.2 * quality, 0.3, 0.97)
                method = RiskMethod.HYBRID
                version = self.registry.version_string()
        urgency = _clip(
            0.45 * distraction + 0.25 * rules.goal_conflict + 0.2 * doomscroll + 0.1 * min(inp.session_minutes / 45.0, 1.0)
        )
        expected_more = _clip(1.5 * inp.avg_session_minutes_14d if inp.avg_session_minutes_14d > 0 else 20.0, 10.0, 60.0)
        loss = continuation * expected_more * (0.4 + 0.6 * max(rules.goal_conflict, distraction))
        return RiskAssessment(
            distraction_risk=round(distraction, 4),
            doomscroll_probability=round(doomscroll, 4),
            goal_conflict=round(rules.goal_conflict, 4),
            intervention_urgency=round(urgency, 4),
            continuation_probability=round(continuation, 4),
            predicted_productivity_loss_minutes=round(loss, 1),
            confidence=round(confidence, 4),
            method=method,
            model_version=version,
            factors=rules.factors,
        )
