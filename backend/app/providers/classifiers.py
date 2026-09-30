"""Layer-2 local content classifiers (section 18): no network, no raw-text storage.

`LexiconClassifier` is the always-available baseline; `SklearnTextClassifier` loads the
TF-IDF + logistic-regression model trained by `ml/training/train_content.py` when its
artifact passes the registry SHA-256 check.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from app.providers.base import ClassifierProvider
from app.schemas.common import ContentCategory as CC

_LEXICON: dict[CC, tuple[tuple[str, float], ...]] = {
    CC.EDUCATION: (
        (r"lectures?", 1.2), (r"courses?", 0.9), (r"tutorials?", 1.0), (r"lessons?", 0.9), (r"explained", 0.8),
        (r"learn(ing)?", 0.6), (r"study( with me| notes| tips)?", 0.7), (r"exams?", 0.6), (r"university|nptel|mit ocw|opencourseware|khan academy", 1.4),
        (r"chapter \d+|class \d+|unit \d+", 0.9), (r"theorem|derivation|proof|equation", 1.0),
        (r"physics|chemistry|calculus|algebra|biology|thermodynamics|fourier|linear algebra", 1.0),
        (r"how to solve|problem set|worked example|revision", 0.9),
    ),
    CC.CAREER: (
        (r"interviews?", 1.1), (r"placements?", 1.3), (r"resume|cv\b", 1.1), (r"jobs?|hiring|recruit(er|ment)", 0.9),
        (r"internships?", 1.1), (r"career", 1.0), (r"salary|offer letter|negotiat", 0.9), (r"system design", 1.0),
        (r"aptitude|mock test|group discussion", 1.0),
    ),
    CC.TECHNOLOGY: (
        (r"programming|coding|code\b", 1.0), (r"python|java\b|javascript|typescript|c\+\+|rust\b|golang", 1.0),
        (r"software|developer|api\b|database|linux|kubernetes|docker|cloud", 0.9),
        (r"machine learning|deep learning|neural network|\bai\b|\bllm", 1.0), (r"algorithms?|data structures?|dsa\b|leetcode", 1.1),
        (r"tech review|unboxing|smartphone|gadget", 0.7),
    ),
    CC.NEWS: (
        (r"breaking|headlines?|live updates?", 1.2), (r"\bnews\b", 1.0), (r"elections?|parliament|minister|government", 1.0),
        (r"economy|inflation|budget session|policy", 0.7), (r"report(s|ed)? that", 0.6),
    ),
    CC.ENTERTAINMENT: (
        (r"funny|comedy|hilarious|lol\b|😂|🤣", 1.1), (r"memes?", 1.2), (r"pranks?", 1.2), (r"celebrit(y|ies)|bollywood|hollywood", 1.0),
        (r"movie trailer|web series|episode \d+|netflix|binge", 0.9), (r"music video|song|dance|reaction video|vlog", 0.8),
        (r"gossip|roast|stand-?up", 1.0), (r"challenge", 0.5),
    ),
    CC.ADVERTISING: (
        (r"sponsored|\bad\b|advertisement", 1.3), (r"\d+% off|discount|sale\b|deal of the day", 1.2),
        (r"buy now|shop now|order now|limited (time )?offer|promo code|coupon", 1.3),
    ),
    CC.GAMING: (
        (r"gameplay|gaming|gamer", 1.2), (r"bgmi|free fire|minecraft|gta|valorant|fortnite|clash of clans|pubg", 1.3),
        (r"let'?s play|walkthrough|speedrun|esports|montage", 1.0),
    ),
    CC.SPORTS: (
        (r"cricket|ipl\b|world cup|test match|odi\b|t20", 1.3), (r"football|fifa|premier league|la liga", 1.2),
        (r"highlights|wickets?|goals? scored|match recap", 0.9), (r"nba|tennis|olympics|kohli|messi|ronaldo", 1.0),
    ),
    CC.SHORT_FORM_VIDEO: (
        (r"#?reels?\b", 1.3), (r"#?shorts\b", 1.3), (r"tiktok|#fyp|foryou", 1.3), (r"trending audio|viral (short|clip)", 1.0),
        (r"\b(15|30|60) ?(sec|seconds)\b", 0.6),
    ),
    CC.CLICKBAIT: (
        (r"you won'?t believe", 1.5), (r"shocking|gone wrong|insane|exposed", 1.1), (r"must watch|what happens next", 1.2),
        (r"!!!+|😱|🔥🔥", 0.7), (r"secret(s)? (they|nobody)", 1.2), (r"top \d+ ", 0.4),
    ),
    CC.SOCIAL: (
        (r"selfie|ootd|birthday|wedding|party|friends", 0.9), (r"weekend vibes|throwback|tbt\b|story time", 1.0),
    ),
    CC.PRODUCTIVITY: (
        (r"productivity|time management|pomodoro|deep work", 1.2), (r"habits?|routine|planner|to-?do", 0.7),
        (r"notion template|focus music|study timer", 1.0),
    ),
}
_COMPILED = {cat: tuple((re.compile(rf"(?<![a-z0-9]){p}", re.IGNORECASE), w) for p, w in terms) for cat, terms in _LEXICON.items()}


class LexiconClassifier(ClassifierProvider):
    name = "lexicon-v1"

    def scores(self, text: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for cat, terms in _COMPILED.items():
            s = sum(w for rx, w in terms if rx.search(text or ""))
            if s > 0:
                out[cat.value] = round(s, 3)
        return out

    def classify(self, text: str) -> tuple[CC, float, dict[str, float]]:
        scores = self.scores(text)
        if not scores:
            return CC.UNKNOWN, 0.0, {}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = (top_score - second) / top_score
        confidence = (1 - math.exp(-0.9 * top_score)) * (0.55 + 0.45 * margin)
        return CC(top), round(min(0.95, confidence), 4), scores


class SklearnTextClassifier(ClassifierProvider):
    """Loads a scikit-learn Pipeline(TfidfVectorizer, classifier with predict_proba)."""

    def __init__(self, model_dir: Path):
        self.model: Any = None
        self.name = "sklearn-unavailable"
        self.error: str | None = None
        registry = Path(model_dir) / "registry.json"
        if not registry.exists():
            self.error = "no registry"
            return
        try:
            entry = (json.loads(registry.read_text()).get("active") or {}).get("content")
        except (OSError, json.JSONDecodeError) as exc:
            self.error = f"registry unreadable: {exc}"
            return
        if not entry:
            self.error = "no active content model"
            return
        path = (Path(model_dir) / entry["path"]).resolve()
        if Path(model_dir).resolve() not in path.parents or not path.exists():
            self.error = "artifact missing or outside model_dir"
            return
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            self.error = "sha256 mismatch"
            return
        import joblib

        self.model = joblib.load(path)  # nosec B301 - integrity verified against registry sha256
        self.name = f"tfidf-lr-{entry.get('version')}"

    @property
    def available(self) -> bool:
        return self.model is not None

    def classify(self, text: str) -> tuple[CC, float, dict[str, float]]:
        if self.model is None or not (text or "").strip():
            return CC.UNKNOWN, 0.0, {}
        proba = self.model.predict_proba([text])[0]
        classes = [str(c) for c in self.model.classes_]
        scores = {c: round(float(p), 4) for c, p in zip(classes, proba, strict=True)}
        best = max(scores, key=lambda c: scores[c])
        return CC(best), scores[best], scores


class HybridClassifier(ClassifierProvider):
    """Prefer the trained model; fall back to the lexicon when the model is unavailable or unsure."""

    def __init__(self, model: SklearnTextClassifier | None, lexicon: LexiconClassifier | None = None, min_confidence: float = 0.45):
        self.model = model if model is not None and model.available else None
        self.lexicon = lexicon or LexiconClassifier()
        self.min_confidence = min_confidence
        self.name = self.model.name if self.model else self.lexicon.name

    def classify(self, text: str) -> tuple[CC, float, dict[str, float]]:
        if self.model is not None:
            category, confidence, scores = self.model.classify(text)
            if confidence >= self.min_confidence:
                return category, confidence, scores
            lex = self.lexicon.classify(text)
            return lex if lex[1] > confidence else (category, confidence, scores)
        return self.lexicon.classify(text)
