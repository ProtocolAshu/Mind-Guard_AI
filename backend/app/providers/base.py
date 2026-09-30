"""Model abstraction layer (section 17): swap vendors without touching agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.schemas.common import ContentCategory


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    purpose: str
    system: str
    messages: tuple[ChatMessage, ...]
    tier: Literal["fast", "reasoning"] = "fast"
    max_tokens: int = 600
    temperature: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    stop_reason: str | None = None


class ProviderError(Exception):
    retryable = False


class ProviderTimeoutError(ProviderError):
    retryable = True


class ProviderUnavailableError(ProviderError):
    retryable = True


class ProviderRejectedError(ProviderError):
    """Authentication / invalid request: retrying will not help."""


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete(self, request: LLMRequest, model: str) -> LLMResponse: ...


class VisionProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def analyze_image(self, image: bytes, media_type: str, prompt: str, system: str, model: str) -> LLMResponse: ...


class EmbeddingProvider(ABC):
    name: str = "abstract"
    model_name: str = "abstract"
    dim: int = 0

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class ClassifierProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def classify(self, text: str) -> tuple[ContentCategory, float, dict[str, float]]: ...
