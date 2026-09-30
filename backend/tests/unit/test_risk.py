import hashlib
import json

import joblib
import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from app.risk.features import RISK_FEATURES, RiskInput, featurize, to_vector
from app.risk.scoring import RiskModelRegistry, RiskScorer, score_rules
from app.schemas.common import AppCategory, ContentCategory, ReasonCode, RiskMethod

BASE = dict(hour=21, app_category=AppCategory.SOCIAL_MEDIA, opens_last_hour=2, history_days=7)


def risk(**kw):
    return RiskScorer().score(RiskInput(**{"session_minutes": 20, **BASE, **kw}))


def test_feature_vector_matches_declared_features():
    f = featurize(RiskInput(session_minutes=10, hour=23, minute=30, scroll_events=40))
    assert list(f) == list(RISK_FEATURES) and len(to_vector(f)) == len(RISK_FEATURES)
    assert f["is_night"] == 1.0 and f["scroll_rate"] == 4.0 and f["minutes_since_last_intervention"] == 240.0


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ({"session_minutes": 5}, {"session_minutes": 45}),
        ({"hour": 15}, {"hour": 23}),
        ({"focus_active": False}, {"focus_active": True}),
        ({"content_category": ContentCategory.EDUCATION, "content_confidence": 0.9},
         {"content_category": ContentCategory.SHORT_FORM_VIDEO, "content_confidence": 0.9}),
        ({"in_restricted_window": False}, {"in_restricted_window": True}),
        ({"long_session_rate_14d": 0.0}, {"long_session_rate_14d": 0.8}),
    ],
)
def test_distraction_risk_is_monotonic_in_key_signals(lower, higher):
    assert risk(**lower).distraction_risk < risk(**higher).distraction_risk


def test_outputs_are_bounded_and_explained():
    r = risk(session_minutes=500, opens_last_hour=500, scroll_events=10**6, daily_social_minutes=10**5)
    for v in (r.distraction_risk, r.doomscroll_probability, r.goal_conflict, r.intervention_urgency,
              r.continuation_probability, r.confidence):
        assert 0.0 <= v <= 1.0
    assert r.method is RiskMethod.RULES and r.factors
    assert r.factors[0].reason_code is ReasonCode.LONG_SESSION
    assert "minutes" in r.factors[0].description


def test_goal_relevant_content_reduces_goal_conflict():
    edu = score_rules(RiskInput(session_minutes=20, hour=10, focus_active=True, in_restricted_window=True,
                                app_category=AppCategory.VIDEO, content_category=ContentCategory.EDUCATION,
                                content_confidence=0.9))
    fun = score_rules(RiskInput(session_minutes=20, hour=10, focus_active=True, in_restricted_window=True,
                                app_category=AppCategory.VIDEO, content_category=ContentCategory.ENTERTAINMENT,
                                content_confidence=0.9))
    assert edu.goal_conflict < 0.5 < fun.goal_conflict


def _write_models(tmp_path, *, tamper=False, features=None, path="risk.joblib"):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(RISK_FEATURES)))
    y = (X[:, 0] > 0).astype(int)
    model = LogisticRegression().fit(X, y)
    artifact = tmp_path / "risk.joblib"
    joblib.dump(model, artifact)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if tamper:
        digest = "0" * 64
    entry = {"path": path, "sha256": digest, "version": "t1", "features": features or list(RISK_FEATURES),
             "metrics": {"roc_auc": 0.9}}
    (tmp_path / "registry.json").write_text(json.dumps({"active": {"distraction": entry}}))
    return tmp_path


def test_verified_model_enables_hybrid_scoring(tmp_path):
    registry = RiskModelRegistry(_write_models(tmp_path))
    assert registry.available and not registry.load_errors
    r = RiskScorer(registry, ml_weight=0.5).score(RiskInput(session_minutes=30, **BASE))
    assert r.method is RiskMethod.HYBRID and r.model_version == "distraction:t1"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [({"tamper": True}, "sha256 mismatch"), ({"features": ["a", "b"]}, "feature list mismatch"),
     ({"path": "../../etc/passwd"}, "escapes model_dir")],
)
def test_untrusted_model_artifacts_are_refused(tmp_path, kwargs, error):
    registry = RiskModelRegistry(_write_models(tmp_path, **kwargs))
    assert not registry.available
    assert any(error in e for e in registry.load_errors)
    assert RiskScorer(registry).score(RiskInput(session_minutes=30, **BASE)).method is RiskMethod.RULES


def test_missing_registry_means_rules_only(tmp_path):
    assert not RiskModelRegistry(tmp_path).available
