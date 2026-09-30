"""Assemble AgentDeps from settings."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.agents.action_agent import AndroidCommandController
from app.agents.runtime import AgentDeps
from app.core.config import Settings
from app.policies.compiler import ConstitutionCompiler
from app.policies.engine import DeterministicPolicyEngine
from app.policies.guardrails import Guardrails
from app.providers.base import LLMProvider, VisionProvider
from app.providers.classifiers import HybridClassifier, SklearnTextClassifier
from app.providers.factory import build_embedding_provider, build_llm_provider, build_vision_provider
from app.providers.gateway import InMemoryTTLCache, RedisResponseCache, ResponseCache
from app.risk.scoring import RiskModelRegistry, RiskScorer
from app.security.tickets import derive_ticket_key
from app.tools.definitions import build_tool_registry

log = logging.getLogger(__name__)
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)
_UNSET: Any = object()


def signing_secret(settings: Settings) -> str:
    secret = settings.jwt_secret.get_secret_value()
    if secret:
        return secret
    if settings.environment == "production":
        raise RuntimeError("JWT_SECRET is required in production")
    log.warning("JWT_SECRET not set: using an ephemeral per-process secret (tokens reset on restart)")
    return _EPHEMERAL_SECRET


def build_agent_deps(settings: Settings, *, llm_provider: LLMProvider | Any | None = _UNSET,
                     vision_provider: VisionProvider | Any | None = _UNSET, redis: Any = None) -> AgentDeps:
    model = SklearnTextClassifier(settings.model_dir)
    registry = RiskModelRegistry(settings.model_dir)
    for error in registry.load_errors:
        log.warning("risk model not loaded: %s", error)
    cache: ResponseCache = RedisResponseCache(redis) if redis is not None else InMemoryTTLCache()
    return AgentDeps(
        settings=settings,
        embedder=build_embedding_provider(settings),
        classifier=HybridClassifier(model),
        risk_scorer=RiskScorer(registry, ml_weight=settings.risk_ml_weight),
        guardrails=Guardrails(),
        compiler=ConstitutionCompiler(),
        ticket_key=derive_ticket_key(signing_secret(settings)),
        tools=build_tool_registry(),
        llm_cache=cache,
        llm_provider=build_llm_provider(settings) if llm_provider is _UNSET else llm_provider,
        vision=build_vision_provider(settings) if vision_provider is _UNSET else vision_provider,
        policy_engine=DeterministicPolicyEngine(),
        interventions=AndroidCommandController(),
    )
