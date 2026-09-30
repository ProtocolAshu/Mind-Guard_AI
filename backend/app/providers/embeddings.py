"""Embedding providers.

`HashingEmbeddingProvider` is the private, offline default: signed feature hashing
of word unigrams/bigrams and character trigrams, L2-normalised. It captures lexical
similarity (not deep semantics) — adequate for retrieving the user's own short
episodic memories and knowledge chunks without sending text to a third party.
`OpenAICompatibleEmbeddingProvider` works with any /v1/embeddings endpoint
(OpenAI, vLLM, Ollama, LM Studio, TEI) when semantic embeddings are preferred.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections.abc import Sequence

import httpx

from app.providers.base import EmbeddingProvider, ProviderError, ProviderRejectedError, ProviderUnavailableError

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


class HashingEmbeddingProvider(EmbeddingProvider):
    name = "hashing"

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.model_name = f"hashing-v1-{dim}"

    def _index(self, token: str) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        return value % self.dim, (1.0 if (value >> 63) & 1 else -1.0)

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = _WORD.findall((text or "").lower())
        counts: dict[str, float] = {}
        for w in words:
            counts[f"w:{w}"] = counts.get(f"w:{w}", 0.0) + 1.0
            padded = f"#{w}#"
            for i in range(len(padded) - 2):
                key = f"c:{padded[i : i + 3]}"
                counts[key] = counts.get(key, 0.0) + 0.35
        for a, b in itertools.pairwise(words):
            counts[f"b:{a}_{b}"] = counts.get(f"b:{a}_{b}", 0.0) + 0.7
        for token, weight in counts.items():
            idx, sign = self._index(token)
            vec[idx] += sign * (1.0 + math.log(weight)) if weight >= 1.0 else sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm > 0 else vec

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, dim: int, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.model_name = model
        self.dim = dim
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
            transport=transport,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            resp = await self._client.post("/embeddings", json={"model": self.model_name, "input": list(texts)})
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("embedding request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"embedding transport error: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403, 400, 404, 422):
            raise ProviderRejectedError(f"embedding endpoint rejected request ({resp.status_code})")
        if resp.status_code >= 500 or resp.status_code == 429:
            raise ProviderUnavailableError(f"embedding endpoint unavailable ({resp.status_code})")
        data = resp.json().get("data", [])
        vectors = [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise ProviderError("embedding response has wrong shape")
        return [[float(x) for x in v] for v in vectors]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
