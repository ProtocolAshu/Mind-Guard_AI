"""Build providers from settings. Missing credentials fall back to the clearly labelled
mock provider in development/test, and fail fast in production."""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.providers.base import EmbeddingProvider, LLMProvider, VisionProvider
from app.providers.embeddings import HashingEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from app.providers.mock import MockLLMProvider, MockVisionProvider

log = logging.getLogger(__name__)


class ProviderConfigError(RuntimeError):
    pass


def _missing(settings: Settings, what: str) -> None:
    if settings.environment == "production":
        raise ProviderConfigError(f"{what} is required in production")
    log.warning("%s missing; using mock provider", what)


def build_llm_provider(settings: Settings) -> LLMProvider | None:
    name = settings.llm_provider
    key = settings.llm_api_key.get_secret_value()
    if name == "disabled":
        return None
    if name == "anthropic":
        if not key:
            _missing(settings, "LLM_API_KEY")
            return MockLLMProvider()
        from app.providers.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(key, base_url=settings.llm_base_url, timeout=settings.llm_timeout_seconds)
    if name == "openai_compatible":
        if not settings.llm_base_url:
            _missing(settings, "LLM_BASE_URL")
            return MockLLMProvider()
        from app.providers.openai_compatible import OpenAICompatibleLLMProvider

        return OpenAICompatibleLLMProvider(settings.llm_base_url, key, timeout=settings.llm_timeout_seconds)
    return MockLLMProvider()


def build_vision_provider(settings: Settings) -> VisionProvider | None:
    name = settings.vision_provider
    key = settings.vision_api_key.get_secret_value()
    if name == "disabled":
        return None
    if name == "anthropic" and key:
        from app.providers.anthropic_provider import AnthropicVisionProvider

        return AnthropicVisionProvider(key, base_url=settings.vision_base_url, timeout=settings.llm_timeout_seconds)
    if name == "openai_compatible" and settings.vision_base_url:
        from app.providers.openai_compatible import OpenAICompatibleVisionProvider

        return OpenAICompatibleVisionProvider(settings.vision_base_url, key, timeout=settings.llm_timeout_seconds)
    if name in ("anthropic", "openai_compatible"):
        _missing(settings, "vision credentials")
    return MockVisionProvider()


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai_compatible" and settings.embedding_base_url:
        return OpenAICompatibleEmbeddingProvider(
            settings.embedding_base_url, settings.embedding_api_key.get_secret_value(), settings.embedding_model,
            settings.embedding_dim,
        )
    return HashingEmbeddingProvider(dim=settings.embedding_dim)
