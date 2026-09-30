"""Shared ML utilities: paths, grouped splits, calibration metrics, integrity-checked registry."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import GroupShuffleSplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "ml" / "datasets" / "synthetic"
MODEL_DIR = REPO_ROOT / "ml" / "models"
REPORT_DIR = REPO_ROOT / "ml" / "evaluation" / "reports"
FIGURE_DIR = REPORT_DIR / "figures"
SEED = 20260917


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_version(path: Path) -> str:
    return datetime.now(UTC).strftime("%Y.%m.%d") + "-" + sha256_file(path)[:8]


def grouped_split(groups: np.ndarray, seed: int = SEED) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """60/20/20 split by group (persona:seed) so no simulated user appears in two splits."""
    idx = np.arange(len(groups))
    outer = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    trainval, test = next(outer.split(idx, groups=groups))
    inner = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed + 1)
    tr, va = next(inner.split(trainval, groups=groups[trainval]))
    return trainval[tr], trainval[va], test


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lo) & (p < hi) if hi < 1.0 else (p >= lo) & (p <= hi)
        if mask.any():
            ece += mask.mean() * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def read_registry() -> dict[str, Any]:
    path = MODEL_DIR / "registry.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"active": {}, "history": []}


def write_registry(registry: dict[str, Any]) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=MODEL_DIR, prefix=".registry-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(registry, fh, indent=2, sort_keys=True)
    os.replace(tmp, MODEL_DIR / "registry.json")
    os.chmod(MODEL_DIR / "registry.json", 0o644)


def register(name: str, entry: dict[str, Any]) -> None:
    registry = read_registry()
    previous = registry["active"].get(name)
    if previous:
        registry["history"].append({"name": name, **previous, "retired_at": datetime.now(UTC).isoformat()})
    registry["active"][name] = entry
    write_registry(registry)
