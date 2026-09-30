"""OpenAI-compatible chat provider (OpenAI, Azure-compatible gateways, vLLM, Ollama,
LM Studio, llama.cpp server). Enables fully local reasoning for privacy-first setups."""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

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


class _OpenAIBase:
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, timeout: float = 12.0, json_mode: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.json_mode = json_mode
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
            transport=transport,
        )

    async def _chat(self, body: dict[str, Any], model: str) -> LLMResponse:
        started = time.perf_counter()
        try:
            resp = await self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("chat completion timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"transport error: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailableError(f"endpoint unavailable ({resp.status_code})")
        if resp.status_code >= 400:
            raise ProviderRejectedError(f"endpoint rejected request ({resp.status_code})")
        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected chat completion response shape") from exc
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model") or model,
            provider=self.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            stop_reason=choice.get("finish_reason"),
        )


class OpenAICompatibleLLMProvider(_OpenAIBase, LLMProvider):
    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "system", "content": request.system}]
            + [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return await self._chat(body, model)


class OpenAICompatibleVisionProvider(_OpenAIBase, VisionProvider):
    async def analyze_image(self, image: bytes, media_type: str, prompt: str, system: str, model: str) -> LLMResponse:
        data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        body = {
            "model": model,
            "max_tokens": 400,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}},
                                             {"type": "text", "text": prompt}]},
            ],
        }
        return await self._chat(body, model)
