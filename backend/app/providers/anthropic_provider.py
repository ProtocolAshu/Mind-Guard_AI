"""Anthropic Messages API provider (text + vision). Retries/backoff are owned by the
gateway, so the SDK client is created with `max_retries=0`."""

from __future__ import annotations

import base64
import time
from typing import Any

from app.providers.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    VisionProvider,
)


def _map_error(exc: Exception) -> ProviderError:
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderTimeoutError("anthropic request timed out")
    if isinstance(exc, anthropic.RateLimitError | anthropic.APIConnectionError | anthropic.InternalServerError):
        return ProviderUnavailableError(f"anthropic unavailable: {type(exc).__name__}")
    if isinstance(
        exc,
        anthropic.AuthenticationError | anthropic.PermissionDeniedError | anthropic.BadRequestError | anthropic.NotFoundError,
    ):
        return ProviderRejectedError(f"anthropic rejected request: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if status >= 500 or status == 529:
            return ProviderUnavailableError(f"anthropic status {status}")
        return ProviderRejectedError(f"anthropic status {status}")
    return ProviderError(f"anthropic error: {type(exc).__name__}")


class _AnthropicBase:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 12.0, client: Any | None = None):
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self._client = client

    async def _create(self, **kwargs: Any) -> tuple[str, Any, float]:
        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except ProviderError:
            raise
        except Exception as exc:
            raise _map_error(exc) from exc
        text = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text")
        return text, response, (time.perf_counter() - started) * 1000

    def _response(self, text: str, response: Any, model: str, latency: float) -> LLMResponse:
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            model=getattr(response, "model", None) or model,
            provider=self.name,
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=latency,
            stop_reason=getattr(response, "stop_reason", None),
        )


class AnthropicLLMProvider(_AnthropicBase, LLMProvider):
    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        text, response, latency = await self._create(
            model=model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            system=request.system,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
        )
        return self._response(text, response, model, latency)


class AnthropicVisionProvider(_AnthropicBase, VisionProvider):
    async def analyze_image(self, image: bytes, media_type: str, prompt: str, system: str, model: str) -> LLMResponse:
        text, response, latency = await self._create(
            model=model,
            max_tokens=400,
            temperature=0.0,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                     "data": base64.b64encode(image).decode("ascii")}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return self._response(text, response, model, latency)
