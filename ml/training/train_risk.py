"""Train, calibrate and register the behavioural risk models (section 18).

Targets: distraction (session becomes unwanted), doomscroll (>= 20 more minutes),
continuation (>= 10 more minutes). Candidates: logistic regression, LightGBM, XGBoost.
Selection by validation ROC-AUC; isotonic calibration on the validation split; final
metrics on a held-out test split of unseen simulated users. The interpretable rules
model is evaluated on the same test rows as the baseline.

    PYTHONPATH=backend:. python -m ml.training.train_risk
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.risk.features import RISK_FEATURES, RiskInput
from app.risk.scoring import TARGETS, score_rules
from ml.common import DATASET_DIR, FIGURE_DIR, MODEL_DIR, REPORT_DIR, SEED, dataset_version, expected_calibration_error, grouped_split, register, sha256_file

RULE_ATTR = {"distraction": "distraction", "doomscroll": "doomscroll", "continuation": "continuation"}


def candidates() -> dict[str, Any]:
    from lightgbm import LGBMClassifier

    models: dict[str, Any] = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=1.0)),
        "lightgbm": LGBMClassifier(n_estimators=400, learning_rate=0.04, num_leaves=31, min_child_samples=40, subsample=0.8,
                                   subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=SEED, verbose=-1),
    }
    try:
        from xgboost import XGBClassifier

        models["xgboost"] = XGBClassifier(n_estimators=400, learning_rate=0.05, max_depth=5, subsample=0.8, colsample_bytree=0.8,
                                          min_child_weight=5, reg_lambda=1.0, random_state=SEED, n_jobs=1, eval_metric="logloss")
    except ImportError:
        pass
    return models


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    pred = (p >= 0.5).astype(int)
    return {"roc_auc": round(float(roc_auc_score(y, p)), 4), "pr_auc": round(float(average_precision_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4), "log_loss": round(float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))), 4),
            "ece": round(expected_calibration_error(y, p), 4), "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
            "recall": round(float(recall_score(y, pred, zero_division=0)), 4), "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
            "positive_rate": round(float(y.mean()), 4), "n": int(len(y))}


def rules_probabilities(frame: pd.DataFrame, target: str) -> np.ndarray:
    dummy = RiskInput(session_minutes=0.0, hour=0)
    return np.asarray([getattr(score_rules(dummy, row), RULE_ATTR[target]) for row in frame[list(RISK_FEATURES)].to_dict("records")])


def main() -> dict[str, Any]:
    data_path = DATASET_DIR / "risk_training.csv"
    df = pd.read_csv(data_path)
    X = df[list(RISK_FEATURES)].to_numpy(dtype=float)
    train, val, test = grouped_split(df["group"].to_numpy())
    version = dataset_version(data_path)
    report: dict[str, Any] = {"dataset": str(data_path.relative_to(MODEL_DIR.parents[1])), "version": version,
                              "splits": {"train": int(len(train)), "val": int(len(val)), "test": int(len(test)),
                                         "test_groups": sorted(set(df["group"].iloc[test]))},
                              "trained_at": datetime.now(UTC).isoformat(), "targets": {}}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    for target in TARGETS:
        y = df[f"label_{target}"].to_numpy(dtype=int)
        results: dict[str, Any] = {"candidates": {}}
        best_name, best_model, best_auc = None, None, -1.0
        for name, model in candidates().items():
            model.fit(X[train], y[train])
            auc = float(roc_auc_score(y[val], model.predict_proba(X[val])[:, 1]))
            results["candidates"][name] = {"val_roc_auc": round(auc, 4),
                                           "test_uncalibrated": metrics(y[test], model.predict_proba(X[test])[:, 1])}
            if auc > best_auc:
                best_name, best_model, best_auc = name, model, auc
        assert best_model is not None and best_name is not None
        calibrated = CalibratedClassifierCV(FrozenEstimator(best_model), method="isotonic").fit(X[val], y[val])
        p_test = calibrated.predict_proba(X[test])[:, 1]
        results["selected"] = best_name
        results["test_calibrated"] = metrics(y[test], p_test)
        rules_p = rules_probabilities(df.iloc[test], target)
        results["rules_baseline_test"] = metrics(y[test], rules_p)
        artifact = MODEL_DIR / f"risk_{target}_{version}.joblib"
        joblib.dump(calibrated, artifact)
        register(target, {"path": artifact.name, "sha256": sha256_file(artifact), "version": version, "algorithm": best_name,
                          "calibration": "isotonic", "features": list(RISK_FEATURES), "metrics": results["test_calibrated"],
                          "trained_at": report["trained_at"]})
        fig, ax = plt.subplots(figsize=(4.8, 4.2))
        for label, probs in (("ML (calibrated)", p_test), ("rules baseline", rules_p)):
            frac, mean = calibration_curve(y[test], probs, n_bins=10, strategy="quantile")
            ax.plot(mean, frac, marker="o", label=label)
        ax.plot([0, 1], [0, 1], linestyle="--", color="grey", label="perfect")
        ax.set(title=f"Reliability: {target} (test)", xlabel="predicted probability", ylabel="observed frequency")
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        fig.savefig(FIGURE_DIR / f"risk_calibration_{target}.png", dpi=130)
        plt.close(fig)
        report["targets"][target] = results
        print(f"{target:13s} selected={best_name:20s} test AUC={results['test_calibrated']['roc_auc']:.3f} "
              f"ECE={results['test_calibrated']['ece']:.3f}  rules AUC={results['rules_baseline_test']['roc_auc']:.3f}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "risk_metrics.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
