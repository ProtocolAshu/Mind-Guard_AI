"""Train and register the content classifier (TF-IDF word + char n-grams, logistic regression).

The lexicon classifier shipped in the backend is evaluated on the same test split as the
baseline. Captions are synthetic and template-based, so absolute scores overstate
real-world accuracy — see the dataset card.

    PYTHONPATH=backend:. python -m ml.training.train_content
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
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

from app.providers.classifiers import LexiconClassifier
from ml.common import DATASET_DIR, FIGURE_DIR, MODEL_DIR, REPORT_DIR, SEED, dataset_version, register, sha256_file


def build(c: float) -> Pipeline:
    return Pipeline([
        ("features", FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, lowercase=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
        ])),
        ("clf", LogisticRegression(max_iter=4000, C=c, class_weight="balanced")),
    ])


def summary(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    return {"accuracy": round(float(accuracy_score(y_true, y_pred)), 4), "macro_precision": round(float(p), 4),
            "macro_recall": round(float(r), 4), "macro_f1": round(float(f), 4),
            "weighted_f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 4), "n": len(y_true)}


def main() -> dict[str, Any]:
    path = DATASET_DIR / "content_captions.csv"
    df = pd.read_csv(path)
    train_df, rest = train_test_split(df, test_size=0.3, stratify=df["label"], random_state=SEED)
    val_df, test_df = train_test_split(rest, test_size=0.5, stratify=rest["label"], random_state=SEED)
    search = {}
    for c in (1.0, 4.0, 10.0):
        model = build(c).fit(train_df["text"], train_df["label"])
        search[c] = f1_score(val_df["label"], model.predict(val_df["text"]), average="macro")
    best_c = max(search, key=lambda k: search[k])
    model = build(best_c).fit(pd.concat([train_df["text"], val_df["text"]]), pd.concat([train_df["label"], val_df["label"]]))
    predictions = list(model.predict(test_df["text"]))
    labels = sorted(df["label"].unique())
    lexicon = LexiconClassifier()
    lexicon_pred = [lexicon.classify(t)[0].value for t in test_df["text"]]
    version = dataset_version(path)
    artifact = MODEL_DIR / f"content_tfidf_lr_{version}.joblib"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, artifact)
    report = {"dataset": "ml/datasets/synthetic/content_captions.csv", "version": version, "trained_at": datetime.now(UTC).isoformat(),
              "splits": {"train": len(train_df), "val": len(val_df), "test": len(test_df)},
              "validation_macro_f1_by_C": {str(k): round(float(v), 4) for k, v in search.items()}, "selected_C": best_c,
              "test": summary(list(test_df["label"]), predictions), "lexicon_baseline_test": summary(list(test_df["label"]), lexicon_pred),
              "per_class": classification_report(test_df["label"], predictions, output_dict=True, zero_division=0)}
    register("content", {"path": artifact.name, "sha256": sha256_file(artifact), "version": version, "algorithm": "tfidf_word_char_logreg",
                         "labels": labels, "metrics": report["test"], "trained_at": report["trained_at"]})
    cm = confusion_matrix(test_df["label"], predictions, labels=labels)
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    ax.imshow(cm / np.maximum(cm.sum(axis=1, keepdims=True), 1), cmap="Blues")
    ax.set_xticks(range(len(labels)), labels, rotation=60, ha="right", fontsize=7)
    ax.set_yticks(range(len(labels)), labels, fontsize=7)
    ax.set(title="Content classifier confusion matrix (test, row-normalised)", xlabel="predicted", ylabel="true")
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "content_confusion_matrix.png", dpi=130)
    plt.close(fig)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "content_metrics.json").write_text(json.dumps(report, indent=2))
    print("content test", report["test"], "\nlexicon baseline", report["lexicon_baseline_test"])
    return report


if __name__ == "__main__":
    main()
