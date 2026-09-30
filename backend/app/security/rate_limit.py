"""Rate limiting: Redis fixed-window counters shared across workers, with an in-process
sliding-window fallback when Redis is not configured or unavailable."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

log = logging.getLogger(__name__)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, max(1, int(window_seconds - (now - bucket[0])) + 1)
        bucket.append(now)
        if len(self._hits) > 50_000:
            for stale in [k for k, v in self._hits.items() if not v][:10_000]:
                del self._hits[stale]
        return True, 0


class RedisRateLimiter:
    def __init__(self, redis: Any, fallback: InMemoryRateLimiter | None = None, prefix: str = "mg:rl:"):
        self.redis = redis
        self.fallback = fallback or InMemoryRateLimiter()
        self.prefix = prefix

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        window = int(now // window_seconds)
        redis_key = f"{self.prefix}{key}:{window}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds + 1)
                count, _ = await pipe.execute()
        except Exception:
            log.warning("redis rate limiter unavailable; using in-process limiter")
            return await self.fallback.hit(key, limit, window_seconds)
        if int(count) > limit:
            return False, max(1, int(window_seconds - (now % window_seconds)))
        return True, 0
