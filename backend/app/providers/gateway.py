"""LLM gateway: the only path from agents to a language model (sections 17, 18, 42).

Responsibilities: model selection by tier, token/cost budgets, response caching and
deduplication, timeouts, retries with backoff, fallback provider/model, strict output
validation, and per-call accounting (`llm_calls` table + Prometheus). It never raises
to agents: failures come back as a typed `GatewayResult` so callers fall back to
deterministic logic (section 24).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.observability import metrics
from app.providers.base import LLMProvider, LLMRequest, LLMResponse, ProviderError, ProviderTimeoutError
from app.providers.structured import extract_json_object
from app.schemas.common import LLMCallStatus

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)  # used by LLMGateway.generate


@dataclass(frozen=True)
class LLMCallRecord:
    run_id: uuid.UUID | None
    user_id: uuid.UUID | None
    provider: str
    model: str
    purpose: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    cost_usd: float
    cache_hit: bool
    status: LLMCallStatus
    error: str | None
    created_at: datetime


@dataclass
class GatewayResult[T: BaseModel]:
    status: LLMCallStatus
    parsed: T | None = None
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_hit: bool = False
    error: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.parsed is not None and self.status in (LLMCallStatus.OK, LLMCallStatus.CACHED)


class ResponseCache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryTTLCache:
    def __init__(self, max_entries: int = 2048):
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.max_entries = max_entries

    async def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._data[key] = (time.monotonic() + ttl_seconds, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


class RedisResponseCache:
    def __init__(self, redis: Any, prefix: str = "mg:llmcache:"):
        self.redis = redis
        self.prefix = prefix

    async def get(self, key: str) -> str | None:
        try:
            value = await self.redis.get(self.prefix + key)
        except Exception:  # cache is best-effort
            return None
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            await self.redis.set(self.prefix + key, value, ex=ttl_seconds)
        except Exception:
            log.warning("llm cache write failed")


UsageLookup = Callable[[uuid.UUID | None], Awaitable[tuple[int, float]]]
Recorder = Callable[[LLMCallRecord], Awaitable[None]]


@dataclass
class GatewayConfig:
    fast_model: str
    reasoning_model: str
    fallback_model: str | None = None
    timeout_seconds: float = 12.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.25
    user_daily_token_budget: int = 40_000
    monthly_budget_usd: float = 25.0
    cache_ttl_seconds: int = 3600
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider | None,
        config: GatewayConfig,
        *,
        fallback_provider: LLMProvider | None = None,
        cache: ResponseCache | None = None,
        usage_lookup: UsageLookup | None = None,
        recorder: Recorder | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.provider = provider
        self.config = config
        self.fallback_provider = fallback_provider
        self.cache = cache
        self.usage_lookup = usage_lookup
        self.recorder = recorder
        self._sleep = sleep

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @property
    def provider_name(self) -> str:
        return self.provider.name if self.provider else "disabled"

    def model_for(self, request: LLMRequest) -> str:
        return self.config.reasoning_model if request.tier == "reasoning" else self.config.fast_model

    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        price = self.config.pricing.get(model) or self.config.pricing.get(model.split(":", 1)[-1])
        if not price:
            return 0.0
        return round(
            prompt_tokens / 1e6 * float(price.get("input_per_mtok", 0.0))
            + completion_tokens / 1e6 * float(price.get("output_per_mtok", 0.0)),
            8,
        )

    def _cache_key(self, request: LLMRequest, model: str, schema: type[BaseModel]) -> str:
        blob = json.dumps(
            {
                "provider": self.provider_name,
                "model": model,
                "purpose": request.purpose,
                "system": request.system,
                "messages": [(m.role, m.content) for m in request.messages],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "schema": schema.__name__,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    async def _record(self, result: GatewayResult[Any], request: LLMRequest, user_id: uuid.UUID | None,
                      run_id: uuid.UUID | None) -> None:
        metrics.LLM_CALLS.labels(result.provider or self.provider_name, request.purpose, result.status.value).inc()
        if result.model:
            metrics.LLM_TOKENS.labels(result.model, "in").inc(result.prompt_tokens)
            metrics.LLM_TOKENS.labels(result.model, "out").inc(result.completion_tokens)
            metrics.LLM_COST.labels(result.model).inc(result.cost_usd)
        metrics.LLM_LATENCY.labels(result.provider or self.provider_name, request.purpose).observe(result.latency_ms / 1000)
        if self.recorder is None:
            return
        try:
            await self.recorder(
                LLMCallRecord(
                    run_id=run_id,
                    user_id=user_id,
                    provider=result.provider or self.provider_name,
                    model=result.model or self.model_for(request),
                    purpose=request.purpose,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    latency_ms=round(result.latency_ms, 2),
                    cost_usd=result.cost_usd,
                    cache_hit=result.cache_hit,
                    status=result.status,
                    error=(result.error or None) and result.error[:500],
                    created_at=datetime.now(UTC),
                )
            )
        except Exception:
            log.exception("failed to record llm call")

    @staticmethod
    def _validate(text: str, schema: type[T]) -> tuple[T | None, str | None]:
        obj = extract_json_object(text)
        if obj is None:
            return None, "no JSON object in model output"
        try:
            return schema.model_validate(obj), None
        except ValidationError as exc:
            return None, f"schema validation failed: {exc.error_count()} error(s)"

    async def _call_with_retries(self, provider: LLMProvider, request: LLMRequest, model: str) -> tuple[LLMResponse | None, ProviderError | None, int]:
        attempts = 0
        last: ProviderError | None = None
        for attempt in range(self.config.max_retries + 1):
            attempts += 1
            try:
                return await asyncio.wait_for(provider.complete(request, model), self.config.timeout_seconds), None, attempts
            except TimeoutError:
                last = ProviderTimeoutError(f"no response within {self.config.timeout_seconds}s")
            except ProviderError as exc:
                last = exc
            except Exception as exc:  # unexpected provider bug: treat as non-retryable
                last = ProviderError(f"provider crashed: {type(exc).__name__}")
            if not last.retryable or attempt == self.config.max_retries:
                break
            delay = self.config.backoff_base_seconds * (2**attempt) * (0.8 + 0.4 * random.random())  # nosec B311 - retry jitter, not security
            await self._sleep(delay)
        return None, last, attempts

    async def generate(
        self,
        request: LLMRequest,
        schema: type[T],
        *,
        user_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        use_cache: bool = True,
    ) -> GatewayResult[T]:
        started = time.perf_counter()
        if self.provider is None:
            result: GatewayResult[T] = GatewayResult(status=LLMCallStatus.ERROR, error="llm provider disabled")
            return result
        model = self.model_for(request)
        if self.usage_lookup is not None:
            try:
                tokens_today, month_cost = await self.usage_lookup(user_id)
            except Exception:
                log.exception("usage lookup failed; failing closed on budget")
                tokens_today, month_cost = self.config.user_daily_token_budget, 0.0
            if tokens_today >= self.config.user_daily_token_budget or month_cost >= self.config.monthly_budget_usd:
                result = GatewayResult(status=LLMCallStatus.BUDGET_EXCEEDED, provider=self.provider_name, model=model,
                                       error="token or cost budget exhausted")
                await self._record(result, request, user_id, run_id)
                return result
        key = self._cache_key(request, model, schema)
        if use_cache and self.cache is not None:
            cached = await self.cache.get(key)
            if cached is not None:
                parsed, _ = self._validate(cached, schema)
                if parsed is not None:
                    result = GatewayResult(status=LLMCallStatus.CACHED, parsed=parsed, provider=self.provider_name,
                                           model=model, cache_hit=True, latency_ms=(time.perf_counter() - started) * 1000)
                    await self._record(result, request, user_id, run_id)
                    return result

        response, error, attempts = await self._call_with_retries(self.provider, request, model)
        provider_name = self.provider_name
        if response is None and self.fallback_provider is not None:
            fb_model = self.config.fallback_model or model
            response, fb_error, fb_attempts = await self._call_with_retries(self.fallback_provider, request, fb_model)
            attempts += fb_attempts
            if response is not None:
                provider_name, model = self.fallback_provider.name, fb_model
            else:
                error = fb_error or error
        elif response is None and self.config.fallback_model and error is not None and not isinstance(error, ProviderTimeoutError):
            response, fb_error, fb_attempts = await self._call_with_retries(self.provider, request, self.config.fallback_model)
            attempts += fb_attempts
            if response is not None:
                model = self.config.fallback_model
            else:
                error = fb_error or error
        latency = (time.perf_counter() - started) * 1000
        if response is None:
            status = LLMCallStatus.TIMEOUT if isinstance(error, ProviderTimeoutError) else LLMCallStatus.ERROR
            result = GatewayResult(status=status, provider=provider_name, model=model, latency_ms=latency,
                                   error=str(error) if error else "unknown provider failure", attempts=attempts)
            await self._record(result, request, user_id, run_id)
            return result

        cost = self.estimate_cost(response.model or model, response.prompt_tokens, response.completion_tokens)
        parsed, validation_error = self._validate(response.text, schema)
        result = GatewayResult(
            status=LLMCallStatus.OK if parsed is not None else LLMCallStatus.INVALID_OUTPUT,
            parsed=parsed,
            provider=provider_name,
            model=response.model or model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost_usd=cost,
            latency_ms=latency,
            error=validation_error,
            attempts=attempts,
        )
        if parsed is not None and use_cache and self.cache is not None:
            await self.cache.set(key, response.text, self.config.cache_ttl_seconds)
        await self._record(result, request, user_id, run_id)
        return result
