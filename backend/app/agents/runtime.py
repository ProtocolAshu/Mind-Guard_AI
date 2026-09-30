"""Process-wide dependencies (`AgentDeps`) and the per-run context (`RunContext`)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.action_agent import AndroidCommandController
from app.core.config import Settings
from app.database.models import UserPreference
from app.interfaces import BehaviorModel, InterventionController, PolicyEngine
from app.policies.compiler import ConstitutionCompiler
from app.policies.engine import DeterministicPolicyEngine
from app.policies.guardrails import Guardrails
from app.providers.base import ClassifierProvider, EmbeddingProvider, LLMProvider, VisionProvider
from app.providers.gateway import GatewayConfig, LLMGateway, ResponseCache
from app.schemas.common import ConsentScope
from app.services.llm_accounting import make_usage_lookup

if TYPE_CHECKING:
    from app.agents.recorder import RunRecorder
    from app.tools.base import ToolRegistry


@dataclass
class AgentDeps:
    settings: Settings
    embedder: EmbeddingProvider
    classifier: ClassifierProvider
    risk_scorer: BehaviorModel
    guardrails: Guardrails
    compiler: ConstitutionCompiler
    ticket_key: bytes
    tools: ToolRegistry
    llm_cache: ResponseCache
    llm_provider: LLMProvider | None = None
    llm_fallback_provider: LLMProvider | None = None
    vision: VisionProvider | None = None
    policy_engine: PolicyEngine = field(default_factory=DeterministicPolicyEngine)
    interventions: InterventionController = field(default_factory=AndroidCommandController)

    def gateway_config(self) -> GatewayConfig:
        s = self.settings
        try:
            pricing = json.loads(s.model_pricing_json or "{}")
        except json.JSONDecodeError:
            pricing = {}
        return GatewayConfig(
            fast_model=s.llm_fast_model,
            reasoning_model=s.llm_reasoning_model,
            fallback_model=s.llm_fallback_model,
            timeout_seconds=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
            user_daily_token_budget=s.llm_user_daily_token_budget,
            monthly_budget_usd=s.llm_monthly_budget_usd,
            cache_ttl_seconds=s.llm_cache_ttl_seconds,
            pricing=pricing if isinstance(pricing, dict) else {},
        )

    def gateway_for(self, session: AsyncSession, recorder: RunRecorder | None, now: datetime) -> LLMGateway:
        return LLMGateway(
            self.llm_provider,
            self.gateway_config(),
            fallback_provider=self.llm_fallback_provider,
            cache=self.llm_cache,
            usage_lookup=make_usage_lookup(session, lambda: now),
            recorder=recorder.llm_call if recorder else None,
        )


@dataclass
class RunContext:
    session: AsyncSession
    deps: AgentDeps
    user_id: uuid.UUID
    now: datetime
    prefs: UserPreference
    consents: frozenset[ConsentScope]
    gateway: LLMGateway
    run_id: uuid.UUID | None = None
    recorder: RunRecorder | None = None
    notes: list[str] = field(default_factory=list)

    def has(self, scope: ConsentScope) -> bool:
        return scope in self.consents

    @property
    def cloud_ai_allowed(self) -> bool:
        return bool(self.prefs.ai_analysis_enabled) and ConsentScope.CLOUD_AI_REASONING in self.consents and self.gateway.enabled
