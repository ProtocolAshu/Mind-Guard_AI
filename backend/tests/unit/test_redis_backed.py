"""Redis-backed rate limiting and LLM response cache against a live server.

Skipped unless TEST_REDIS_URL is set (CI and docker-compose provide one):
    TEST_REDIS_URL=redis://localhost:6379/1 pytest tests/unit/test_redis_backed.py
"""

import os
import uuid
from typing import Any

import pytest

from app.providers.gateway import RedisResponseCache
from app.security.rate_limit import InMemoryRateLimiter, RedisRateLimiter

REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = [pytest.mark.redis, pytest.mark.skipif(not REDIS_URL, reason="set TEST_REDIS_URL to run Redis tests")]


@pytest.fixture
async def redis_client() -> Any:
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, socket_timeout=2.0, socket_connect_timeout=2.0)
    await client.ping()
    yield client
    await client.aclose()


class BrokenRedis:
    """Stands in for an unreachable server: every call raises."""

    def pipeline(self, transaction: bool = True) -> Any:
        raise ConnectionError("redis down")

    async def get(self, key: str) -> Any:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None) -> Any:
        raise ConnectionError("redis down")


async def test_rate_limit_is_shared_between_workers(redis_client):
    key = f"test:{uuid.uuid4().hex[:8]}"
    worker_a = RedisRateLimiter(redis_client, prefix="mg:test:")
    worker_b = RedisRateLimiter(redis_client, prefix="mg:test:")
    assert await worker_a.hit(key, limit=3, window_seconds=60) == (True, 0)
    assert await worker_b.hit(key, limit=3, window_seconds=60) == (True, 0)
    assert await worker_a.hit(key, limit=3, window_seconds=60) == (True, 0)
    allowed, retry_after = await worker_b.hit(key, limit=3, window_seconds=60)
    assert not allowed and 0 < retry_after <= 60  # the fourth request is refused by the other worker's counter
    other = await worker_a.hit(f"test:{uuid.uuid4().hex[:8]}", limit=3, window_seconds=60)
    assert other == (True, 0)  # limits are per key


async def test_rate_limiter_window_key_expires(redis_client):
    key = f"test:{uuid.uuid4().hex[:8]}"
    limiter = RedisRateLimiter(redis_client, prefix="mg:test:")
    await limiter.hit(key, limit=1, window_seconds=1)
    keys = [k async for k in redis_client.scan_iter(match=f"mg:test:{key}:*")]
    assert len(keys) == 1
    assert 0 < await redis_client.ttl(keys[0]) <= 2


async def test_rate_limiter_falls_back_when_redis_is_down():
    fallback = InMemoryRateLimiter()
    limiter = RedisRateLimiter(BrokenRedis(), fallback=fallback)
    assert await limiter.hit("k", limit=1, window_seconds=60) == (True, 0)
    allowed, _ = await limiter.hit("k", limit=1, window_seconds=60)
    assert not allowed  # the in-process limiter still enforces the limit


async def test_response_cache_roundtrip_and_ttl(redis_client):
    cache = RedisResponseCache(redis_client, prefix=f"mg:test:{uuid.uuid4().hex[:8]}:")
    assert await cache.get("missing") is None
    await cache.set("k", '{"decision":"ALLOW"}', ttl_seconds=30)
    assert await cache.get("k") == '{"decision":"ALLOW"}'
    assert 0 < await redis_client.ttl(cache.prefix + "k") <= 30


async def test_response_cache_is_best_effort_when_redis_is_down():
    cache = RedisResponseCache(BrokenRedis())
    await cache.set("k", "v", ttl_seconds=10)  # must not raise
    assert await cache.get("k") is None
