"""Contextual bandits for personalised intervention selection (sections 4.10, 9).

Safety model: the bandit never chooses freely. The decision ladder computes the
*eligible* arms (bounded by policy, risk band and style) and a *prior* arm; the
bandit only personalises inside that set, follows the prior during warm-up, and
breaks ties toward the gentler intervention. Guardrails still authorize the result.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from app.schemas.common import (
    ENTERTAINMENT_LIKE,
    INTERVENTION_SEVERITY,
    AppCategory,
    InterventionStyle,
    InterventionType,
)

ARMS: tuple[InterventionType, ...] = tuple(InterventionType)
CONTEXT_FEATURES: tuple[str, ...] = (
    "bias", "risk", "goal_conflict", "hour_sin", "hour_cos", "night", "focus_mode", "session_norm", "social_app",
    "video_app", "has_goal", "entertainment", "prev_result", "override_rate", "style_gentle", "style_strict",
)
N_FEATURES = len(CONTEXT_FEATURES)


@dataclass(frozen=True)
class BanditContext:
    risk: float
    goal_conflict: float
    hour: int
    focus_mode: bool
    session_minutes: float
    app_category: AppCategory
    has_goal: bool
    content_category: str
    prev_result: float = 0.0
    override_rate: float = 0.0
    style: InterventionStyle = InterventionStyle.BALANCED


def context_vector(ctx: BanditContext) -> list[float]:
    angle = 2 * math.pi * (ctx.hour % 24) / 24.0
    return [
        1.0,
        float(ctx.risk),
        float(ctx.goal_conflict),
        math.sin(angle),
        math.cos(angle),
        float(ctx.hour >= 22 or ctx.hour < 5),
        float(ctx.focus_mode),
        min(ctx.session_minutes / 60.0, 1.5),
        float(ctx.app_category is AppCategory.SOCIAL_MEDIA),
        float(ctx.app_category is AppCategory.VIDEO),
        float(ctx.has_goal),
        float(ctx.content_category in {c.value for c in ENTERTAINMENT_LIKE}),
        max(-1.0, min(1.0, ctx.prev_result)),
        max(0.0, min(1.0, ctx.override_rate)),
        float(ctx.style is InterventionStyle.GENTLE),
        float(ctx.style is InterventionStyle.STRICT),
    ]


@dataclass(frozen=True)
class BanditChoice:
    arm: InterventionType
    scores: dict[str, float]
    explored: bool
    reason: str


class LinearBandit:
    """Disjoint linear model per arm (ridge regression sufficient statistics)."""

    algorithm: ClassVar[str] = "linear"

    def __init__(self, n_features: int = N_FEATURES, ridge: float = 1.0, warmup: int = 4, prior_bonus: float = 0.1):
        self.d = n_features
        self.ridge = ridge
        self.warmup = warmup
        self.prior_bonus = prior_bonus
        self.A: dict[str, np.ndarray] = {a.value: ridge * np.eye(n_features) for a in ARMS}
        self.b: dict[str, np.ndarray] = {a.value: np.zeros(n_features) for a in ARMS}
        self.counts: dict[str, int] = {a.value: 0 for a in ARMS}
        self.reward_sum: dict[str, float] = {a.value: 0.0 for a in ARMS}
        self.total_updates = 0

    # -- persistence -------------------------------------------------------
    def to_state(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "n_features": self.d,
            "ridge": self.ridge,
            "A": {k: v.round(6).tolist() for k, v in self.A.items()},
            "b": {k: v.round(6).tolist() for k, v in self.b.items()},
            "counts": dict(self.counts),
            "reward_sum": {k: round(v, 6) for k, v in self.reward_sum.items()},
            "total_updates": self.total_updates,
            **self._extra_state(),
        }

    def _extra_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, state: dict[str, Any]) -> bool:
        """Restore; returns False (and keeps a fresh model) if the stored state is incompatible."""
        try:
            if state.get("n_features") != self.d:
                return False
            A = {k: np.asarray(v, dtype=float) for k, v in state["A"].items()}
            b = {k: np.asarray(v, dtype=float) for k, v in state["b"].items()}
            if any(m.shape != (self.d, self.d) for m in A.values()) or any(v.shape != (self.d,) for v in b.values()):
                return False
            for arm in ARMS:
                if arm.value in A:
                    self.A[arm.value], self.b[arm.value] = A[arm.value], b[arm.value]
            self.counts.update({k: int(v) for k, v in state.get("counts", {}).items() if k in self.counts})
            self.reward_sum.update({k: float(v) for k, v in state.get("reward_sum", {}).items() if k in self.reward_sum})
            self.total_updates = int(state.get("total_updates", 0))
            return True
        except (KeyError, TypeError, ValueError):
            return False

    # -- learning ----------------------------------------------------------
    def theta(self, arm: str) -> np.ndarray:
        return np.linalg.solve(self.A[arm], self.b[arm])

    def update(self, arm: InterventionType, x: Sequence[float], reward: float) -> None:
        v = np.asarray(x, dtype=float)
        if v.shape != (self.d,):
            raise ValueError(f"context has {v.shape}, expected ({self.d},)")
        r = max(-1.0, min(1.0, float(reward)))
        self.A[arm.value] += np.outer(v, v)
        self.b[arm.value] += r * v
        self.counts[arm.value] += 1
        self.reward_sum[arm.value] += r
        self.total_updates += 1

    def _arm_scores(self, x: np.ndarray, arm: str) -> float:
        raise NotImplementedError

    def select(
        self, x: Sequence[float], eligible: Sequence[InterventionType], prior: InterventionType
    ) -> BanditChoice:
        if not eligible:
            raise ValueError("eligible arm set must not be empty")
        v = np.asarray(x, dtype=float)
        if prior in eligible and self.total_updates < self.warmup:
            return BanditChoice(prior, {}, False, f"warm-up ({self.total_updates}/{self.warmup} outcomes): policy default")
        scores = {arm.value: self._arm_scores(v, arm.value) + (self.prior_bonus if arm == prior else 0.0) for arm in eligible}
        best = min(eligible, key=lambda a: (-round(scores[a.value], 9), INTERVENTION_SEVERITY[a]))
        greedy = max(eligible, key=lambda a: (float(v @ self.theta(a.value)), -INTERVENTION_SEVERITY[a]))
        explored = best != greedy
        return BanditChoice(best, {k: round(s, 4) for k, s in scores.items()}, explored, self._reason(best, prior))

    def _reason(self, arm: InterventionType, prior: InterventionType) -> str:
        n = self.counts[arm.value]
        mean = self.reward_sum[arm.value] / n if n else 0.0
        if arm == prior:
            return f"policy default confirmed by outcomes (n={n}, mean reward {mean:+.2f})"
        return f"personalised: {arm.value} has worked better for you (n={n}, mean reward {mean:+.2f})"


class LinUCB(LinearBandit):
    algorithm = "linucb"

    def __init__(self, *args: Any, alpha: float = 0.6, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.alpha = alpha

    def _extra_state(self) -> dict[str, Any]:
        return {"alpha": self.alpha}

    def _arm_scores(self, x: np.ndarray, arm: str) -> float:
        a_inv_x = np.linalg.solve(self.A[arm], x)
        alpha = self.alpha * max(0.25, 1.0 / math.sqrt(1.0 + self.total_updates / 50.0))
        return float(x @ self.theta(arm) + alpha * math.sqrt(max(0.0, float(x @ a_inv_x))))


class EpsilonGreedy(LinearBandit):
    algorithm = "epsilon_greedy"

    def __init__(self, *args: Any, epsilon: float = 0.1, seed: int | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.epsilon = epsilon
        self.rng = random.Random(seed)  # nosec B311 - exploration sampling, not security

    def _arm_scores(self, x: np.ndarray, arm: str) -> float:
        return float(x @ self.theta(arm))

    def select(self, x: Sequence[float], eligible: Sequence[InterventionType], prior: InterventionType) -> BanditChoice:
        if eligible and self.total_updates >= self.warmup and self.rng.random() < self.epsilon:
            arm = self.rng.choice(list(eligible))
            return BanditChoice(arm, {}, True, "exploration within the safe action set")
        return super().select(x, eligible, prior)


class LinearThompson(LinearBandit):
    algorithm = "thompson"

    def __init__(self, *args: Any, v: float = 0.5, seed: int | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.v = v
        self.np_rng = np.random.default_rng(seed)

    def _arm_scores(self, x: np.ndarray, arm: str) -> float:
        cov = self.v**2 * np.linalg.inv(self.A[arm])
        sample = self.np_rng.multivariate_normal(self.theta(arm), (cov + cov.T) / 2.0)
        return float(x @ sample)


ALGORITHMS: dict[str, type[LinearBandit]] = {"linucb": LinUCB, "epsilon_greedy": EpsilonGreedy, "thompson": LinearThompson}


def make_bandit(algorithm: str = "linucb", state: dict[str, Any] | None = None, **kwargs: Any) -> LinearBandit:
    cls = ALGORITHMS.get(algorithm, LinUCB)
    bandit = cls(**kwargs)
    if state:
        bandit.load_state(state)
    return bandit
