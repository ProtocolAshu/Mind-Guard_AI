"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.middleware import MindGuardMiddleware
from app.api.routes import auth, goals, insights, interventions, policies, system, usage, users
from app.container import Container, build_container
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database.session import create_all
from app.observability.tracing import configure_tracing
from app.rag.knowledge import sync_corpus

log = logging.getLogger(__name__)

TAGS = [
    {"name": "Authentication", "description": "Registration, login, rotating refresh tokens."},
    {"name": "Interventions", "description": "Run the agent graph for a live app session."},
    {"name": "Policies", "description": "Personal AI Constitution: compile, save, version, override."},
    {"name": "Agent status", "description": "Agent traces, prompts, tools and graph topology."},
]


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_tracing(settings.otel_service_name, settings.otel_endpoint)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        c = container or await build_container(settings)
        app.state.container = c
        if settings.database_url.startswith("sqlite") and settings.environment != "production":
            await create_all(c.engine)  # PostgreSQL deployments use Alembic migrations
        try:
            async with c.session_factory() as session:
                indexed = await sync_corpus(session, c.deps.embedder, settings.knowledge_dir)
                await session.commit()
                if indexed:
                    log.info("knowledge corpus indexed", extra={"documents": indexed})
        except Exception:
            log.exception("knowledge corpus sync failed; retrieval will be empty")
        yield
        if owned:
            await c.close()

    app = FastAPI(title="MindGuard API", version=settings.api_version, lifespan=lifespan, openapi_tags=TAGS,
                  description="Autonomous multi-agent AI attention firewall. All actions are proposals until authorized "
                              "by the deterministic guardrail engine.")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-ID"], max_age=600)
    app.add_middleware(MindGuardMiddleware, max_body_bytes=settings.max_request_bytes)
    install_error_handlers(app)
    for module in (system, auth, users, goals, policies, usage, interventions, insights):
        app.include_router(module.router)
    return app


app = create_app()
