"""Replaceable component contracts (section 52: no vendor lock-in).

LLMProvider, VisionProvider and EmbeddingProvider are defined in app.providers.base and re-exported here.
BehaviorModel, PolicyEngine and InterventionController are defined below. The agent runtime depends only on these
abstractions (see AgentDeps), so an implementation is swapped in app/agents/factory.py without touching agents or tools.

| Interface              | Implementations                                                        |
|------------------------|------------------------------------------------------------------------|
| LLMProvider            | AnthropicProvider, OpenAICompatibleProvider, MockLLMProvider           |
| VisionProvider         | AnthropicVisionProvider, MockVisionProvider                            |
| EmbeddingProvider      | HashingEmbeddingProvider (offline), OpenAI-compatible embeddings       |
| BehaviorModel          | RiskScorer (interpretable logistic rules + calibrated GBM hybrid)      |
| PolicyEngine           | DeterministicPolicyEngine (typed rule DSL)                             |
| InterventionController | AndroidCommandController (server); android/intervention (device side)  |
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.providers.base import EmbeddingProvider, LLMProvider, VisionProvider

if TYPE_CHECKING:
    from app.risk.features import RiskInput
    from app.schemas.agents import DeviceCommand, RiskAssessment
    from app.schemas.common import InterventionType
    from app.schemas.policy import PolicyContext, PolicyRule, PolicyVerdict, ValidationReport

__all__ = ["BehaviorModel", "EmbeddingProvider", "InterventionController", "LLMProvider", "PolicyEngine", "VisionProvider"]


class BehaviorModel(ABC):
    """Predicts distraction, doomscrolling and continuation risk for one moment of app use."""

    name: str = "behavior-model"

    @abstractmethod
    def score(self, inp: RiskInput) -> RiskAssessment:
        """Must never raise for valid input; fall back to a conservative estimate instead."""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "ml_available": False, "versions": None, "load_errors": [], "ml_weight": None}


class PolicyEngine(ABC):
    """Evaluates, validates and explains the user's rules. Must be deterministic: the guardrail layer relies on it."""

    name: str = "policy-engine"

    @abstractmethod
    def evaluate(self, rules: Sequence[PolicyRule], ctx: PolicyContext) -> PolicyVerdict: ...

    @abstractmethod
    def validate(self, rules: Sequence[PolicyRule]) -> ValidationReport: ...

    @abstractmethod
    def explain(self, rule: PolicyRule) -> str: ...


class InterventionController(ABC):
    """Server half of intervention delivery: turns a guardrail-approved decision into a command for a device.
    The device half executes it with platform APIs (android/intervention InterventionController)."""

    name: str = "intervention-controller"

    @abstractmethod
    def build(self, decision: InterventionType, duration_minutes: int, *, intervention_id: uuid.UUID | None,
              app_package: str | None, title: str, body: str, now: datetime) -> DeviceCommand: ...

    @abstractmethod
    def expiry(self, decision: InterventionType, duration_minutes: int, now: datetime) -> datetime | None: ...
