"""Reward definition (section 9): positive when the unwanted session stops or the
user returns to the task; negative for immediate override, continued scrolling,
or disabling protection."""

from __future__ import annotations

from app.schemas.common import OutcomeType

BASE_REWARD: dict[OutcomeType, float] = {
    OutcomeType.ACCEPTED: 1.0,
    OutcomeType.STOPPED_SESSION: 1.0,
    OutcomeType.RETURNED_TO_TASK: 1.0,
    OutcomeType.IGNORED: -0.2,
    OutcomeType.CONTINUED_SESSION: -0.5,
    OutcomeType.OVERRIDDEN: -1.0,
    OutcomeType.DISABLED_PROTECTION: -1.0,
}


def compute_reward(outcome: OutcomeType, satisfaction: int | None = None) -> float:
    reward = BASE_REWARD[outcome]
    if satisfaction is not None:
        reward += (max(1, min(5, satisfaction)) - 3) * 0.125
    return round(max(-1.0, min(1.0, reward)), 4)
