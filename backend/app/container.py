"""Application container: process-wide singletons created in the FastAPI lifespan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.factory import build_agent_deps, signing_secret
from app.agents.runtime import AgentDeps
from app.agents.supervisor import Orchestrator
from app.core.clock import Clock
from app.core.config import Settings
from app.database.session import create_engine, create_session_factory
from app.security.rate_limit import InMemoryRateLimiter, RedisRateLimiter

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    deps: AgentDeps
    orchestrator: Orchestrator
    rate_limiter: InMemoryRateLimiter | RedisRateLimiter
    clock: Clock
    jwt_secret: str
    redis: Any = None

    async def close(self) -> None:
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception:
                log.warning("redis close failed")
        await self.engine.dispose()


async def build_container(settings: Settings, *, clock: Clock | None = None, engine: AsyncEngine | None = None,
                          deps: AgentDeps | None = None) -> Container:
    redis = None
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
            await redis.ping()
        except Exception:
            log.warning("redis unavailable; continuing with in-process cache and rate limits")
            redis = None
    engine = engine or create_engine(settings.database_url, echo=settings.database_echo)
    factory = create_session_factory(engine)
    deps = deps or build_agent_deps(settings, redis=redis)
    limiter: InMemoryRateLimiter | RedisRateLimiter = RedisRateLimiter(redis) if redis is not None else InMemoryRateLimiter()
    return Container(settings=settings, engine=engine, session_factory=factory, deps=deps,
                     orchestrator=Orchestrator(deps, factory), rate_limiter=limiter, clock=clock or Clock(),
                     jwt_secret=signing_secret(settings), redis=redis)
