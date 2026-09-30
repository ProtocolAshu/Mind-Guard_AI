"""Section 52: every replaceable component is consumed through its interface, so swapping it changes behaviour."""

from collections.abc import Sequence

import pytest

from app.agents.action_agent import AndroidCommandController
from app.agents.factory import build_agent_deps
from app.agents.supervisor import Orchestrator
from app.interfaces import (
    BehaviorModel,
    EmbeddingProvider,
    InterventionController,
    LLMProvider,
    PolicyEngine,
    VisionProvider,
)
from app.policies.engine import DeterministicPolicyEngine
from app.providers.mock import MockLLMProvider, MockVisionProvider
from app.risk.scoring import RiskScorer
from app.schemas.agents import RiskAssessment
from app.schemas.common import InterventionType as IT
from app.schemas.common import RiskMethod
from tests.agents.test_scenarios import IG, evaluate, policy_from_text
from tests.conftest import ist, seed_user


class ConstantRisk(BehaviorModel):
    name = "constant-high"

    def score(self, inp):
        return RiskAssessment(distraction_risk=0.96, doomscroll_probability=0.9, goal_conflict=0.5, intervention_urgency=0.9,
                              continuation_probability=0.9, predicted_productivity_loss_minutes=30, confidence=0.9, method=RiskMethod.RULES)


class SpyEngine(DeterministicPolicyEngine):
    def __init__(self) -> None:
        self.evaluations = 0
        self.validations = 0

    def evaluate(self, rules: Sequence, ctx):
        self.evaluations += 1
        return super().evaluate(rules, ctx)

    def validate(self, rules: Sequence):
        self.validations += 1
        return super().validate(rules)


class SpyController(AndroidCommandController):
    def __init__(self) -> None:
        self.built: list[IT] = []

    def build(self, decision, duration_minutes, **kwargs):
        self.built.append(decision)
        return super().build(decision, duration_minutes, **kwargs)


def test_default_implementations_satisfy_the_contracts(settings):
    deps = build_agent_deps(settings, llm_provider=MockLLMProvider(), vision_provider=MockVisionProvider())
    assert isinstance(deps.risk_scorer, BehaviorModel) and isinstance(deps.risk_scorer, RiskScorer)
    assert isinstance(deps.policy_engine, PolicyEngine) and isinstance(deps.interventions, InterventionController)
    assert isinstance(deps.llm_provider, LLMProvider) and isinstance(deps.vision, VisionProvider) and isinstance(deps.embedder, EmbeddingProvider)
    with pytest.raises(TypeError):
        PolicyEngine()  # type: ignore[abstract]


async def test_swapping_the_behavior_model_changes_decisions(settings, session_factory, session):
    now = ist(2026, 9, 14, 13)
    uid = await seed_user(session)
    default = Orchestrator(build_agent_deps(settings, llm_provider=MockLLMProvider(), vision_provider=MockVisionProvider()), session_factory)
    assert (await evaluate(default, session, uid, now, minutes=2)).decision is IT.ALLOW

    deps = build_agent_deps(settings, llm_provider=MockLLMProvider(), vision_provider=MockVisionProvider())
    deps.risk_scorer = ConstantRisk()
    uid2 = await seed_user(session)
    swapped = await evaluate(Orchestrator(deps, session_factory), session, uid2, now, minutes=2)
    assert swapped.decision is not IT.ALLOW and swapped.risk.distraction_risk == 0.96


async def test_policy_engine_and_intervention_controller_are_called_through_deps(settings, session_factory, session):
    deps = build_agent_deps(settings, llm_provider=MockLLMProvider(), vision_provider=MockVisionProvider())
    engine, controller = SpyEngine(), SpyController()
    deps.policy_engine, deps.interventions = engine, controller
    orch = Orchestrator(deps, session_factory)
    now = ist(2026, 9, 14, 21)
    uid = await seed_user(session, style="strict")
    await policy_from_text(orch, session, uid, "Block Instagram strictly after 8 PM.", now)
    assert engine.validations >= 1
    response = await evaluate(orch, session, uid, now, app=IG, minutes=10)
    assert response.decision is IT.TEMPORARY_BLOCK
    assert engine.evaluations >= 1 and controller.built == [IT.TEMPORARY_BLOCK]
