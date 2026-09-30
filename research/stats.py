"""Statistics for paired simulation experiments."""

from __future__ import annotations

import numpy as np
from scipy import stats


def bootstrap_ci(values: np.ndarray, n: int = 5000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(n, len(values)), replace=True).mean(axis=1)
    return float(values.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def paired_comparison(a: np.ndarray, b: np.ndarray, seed: int = 0) -> dict[str, float]:
    """a - b over matched episodes: mean difference with bootstrap CI, Wilcoxon signed-rank p, rank-biserial r."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    diff = a[mask] - b[mask]
    if len(diff) == 0:  # metric undefined for one controller (e.g. override rate without interventions)
        return {"mean_diff": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "p_value": 1.0, "rank_biserial": float("nan"), "n": 0}
    mean, lo, hi = bootstrap_ci(diff, seed=seed)
    nonzero = diff[diff != 0]
    if len(nonzero) < 2:
        return {"mean_diff": mean, "ci_lo": lo, "ci_hi": hi, "p_value": 1.0, "rank_biserial": 0.0, "n": int(mask.sum())}
    p = float(stats.wilcoxon(nonzero).pvalue)
    ranks = stats.rankdata(np.abs(nonzero))
    w_pos, w_neg = ranks[nonzero > 0].sum(), ranks[nonzero < 0].sum()
    return {"mean_diff": mean, "ci_lo": lo, "ci_hi": hi, "p_value": p, "rank_biserial": float((w_pos - w_neg) / (w_pos + w_neg)),
            "n": int(mask.sum())}


def holm(p_values: list[float]) -> list[float]:
    order = np.argsort(p_values)
    m = len(p_values)
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[idx]))
        adjusted[idx] = running
    return adjusted.tolist()
