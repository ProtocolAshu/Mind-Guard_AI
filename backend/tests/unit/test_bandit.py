import random

import pytest

from app.bandit.policies import (
    N_FEATURES,
    BanditContext,
    EpsilonGreedy,
    LinearThompson,
    LinUCB,
    context_vector,
    make_bandit,
)
from app.bandit.rewards import compute_reward
from app.schemas.common import AppCategory, OutcomeType
from app.schemas.common import InterventionType as IT

CTX = context_vector(BanditContext(risk=0.8, goal_conflict=0.6, hour=23, focus_mode=False, session_minutes=35,
                                   app_category=AppCategory.SOCIAL_MEDIA, has_goal=True, content_category="entertainment"))
ELIGIBLE = [IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK]


def simulate(bandit, success, rounds=120, seed=1):
    rng = random.Random(seed)
    picks = []
    for _ in range(rounds):
        choice = bandit.select(CTX, ELIGIBLE, prior=IT.TEMPORARY_BLOCK)
        assert choice.arm in ELIGIBLE
        reward = 1.0 if rng.random() < success[choice.arm] else -1.0
        bandit.update(choice.arm, CTX, reward)
        picks.append(choice.arm)
    return picks


@pytest.mark.parametrize("factory", [lambda: LinUCB(), lambda: EpsilonGreedy(seed=3), lambda: LinearThompson(seed=3)])
def test_bandits_learn_the_intervention_that_works_for_this_user(factory):
    success = {IT.DELAY: 0.85, IT.LIMITED_ACCESS: 0.45, IT.TEMPORARY_BLOCK: 0.15}
    picks = simulate(factory(), success)
    late = picks[-40:]
    assert late.count(IT.DELAY) / len(late) >= 0.7


def test_warmup_follows_policy_prior_and_respects_eligibility():
    bandit = LinUCB(warmup=4)
    for _ in range(4):
        choice = bandit.select(CTX, ELIGIBLE, prior=IT.TEMPORARY_BLOCK)
        assert choice.arm is IT.TEMPORARY_BLOCK and "warm-up" in choice.reason
        bandit.update(choice.arm, CTX, -1.0)
    assert bandit.select(CTX, [IT.SOFT_WARNING], prior=IT.TEMPORARY_BLOCK).arm is IT.SOFT_WARNING
    with pytest.raises(ValueError):
        bandit.select(CTX, [], prior=IT.DELAY)


def test_state_round_trip_reproduces_decisions_and_rejects_incompatible_state():
    bandit = LinUCB()
    simulate(bandit, {IT.DELAY: 0.9, IT.LIMITED_ACCESS: 0.2, IT.TEMPORARY_BLOCK: 0.1}, rounds=30)
    restored = make_bandit("linucb", bandit.to_state())
    assert restored.total_updates == 30
    assert restored.select(CTX, ELIGIBLE, IT.TEMPORARY_BLOCK).arm == bandit.select(CTX, ELIGIBLE, IT.TEMPORARY_BLOCK).arm
    fresh = LinUCB()
    assert not fresh.load_state({"n_features": N_FEATURES + 1, "A": {}, "b": {}})
    assert not fresh.load_state({"n_features": N_FEATURES, "A": {"DELAY": [[1]]}, "b": {"DELAY": [0]}})
    assert fresh.total_updates == 0


def test_update_validates_context_shape_and_clips_reward():
    bandit = LinUCB()
    with pytest.raises(ValueError):
        bandit.update(IT.DELAY, [1.0, 2.0], 1.0)
    bandit.update(IT.DELAY, CTX, 50.0)
    assert bandit.reward_sum["DELAY"] == 1.0


def test_reward_signals():
    assert compute_reward(OutcomeType.ACCEPTED) == 1.0
    assert compute_reward(OutcomeType.OVERRIDDEN) == -1.0
    assert compute_reward(OutcomeType.CONTINUED_SESSION) == -0.5
    assert compute_reward(OutcomeType.ACCEPTED, satisfaction=1) == 0.75
    assert compute_reward(OutcomeType.OVERRIDDEN, satisfaction=5) == -0.75
