import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
IS_PG = TEST_DB_URL.startswith("postgresql")


@pytest.fixture
async def engine():
    from app.database import models  # noqa: F401  (register every table before create_all)
    from app.database.base import Base
    from app.database.session import create_engine

    engine = create_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        if IS_PG:
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    if IS_PG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    from app.database.session import create_session_factory

    return create_session_factory(engine)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def settings(tmp_path):
    from app.core.config import Settings

    return Settings(environment="test", jwt_secret="t" * 48, model_dir=tmp_path / "models", llm_provider="mock",
                    database_url=TEST_DB_URL, log_json=False)


ALL_CONSENTS = ("usage_monitoring", "content_text_analysis", "memory_personalization", "cloud_ai_reasoning")


async def seed_user(session, *, email=None, consents=ALL_CONSENTS, tz="Asia/Kolkata", style="balanced", guardian=True,
                    content_analysis=True):
    from app.database.models import Consent, User, UserPreference

    user = User(email=email or f"u{uuid.uuid4().hex[:10]}@example.com", password_hash="x", display_name="Test")
    session.add(user)
    await session.flush()
    session.add(UserPreference(user_id=user.id, timezone=tz, intervention_style=style, guardian_enabled=guardian,
                               content_analysis_enabled=content_analysis))
    for scope in consents:
        session.add(Consent(user_id=user.id, scope=scope, granted=True))
    await session.commit()
    return user.id


def ist(y, mo, d, h, mi=0):
    from zoneinfo import ZoneInfo

    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)


# ----------------------------------------------------------------------------- API fixtures
PASSWORD = "correct horse 42 battery"


@pytest.fixture
def api_settings(settings):
    return settings.model_copy(update={"rate_limit_auth_per_minute": 1000, "rate_limit_llm_per_minute": 1000,
                                       "rate_limit_default_per_minute": 5000, "admin_emails": "admin@example.com",
                                       "max_request_bytes": 200_000})


@pytest.fixture
def clock():
    from app.core.clock import FrozenClock

    return FrozenClock(datetime(2026, 9, 14, 15, 30, tzinfo=UTC))  # 21:00 IST, Monday


@pytest.fixture
async def container(api_settings, engine, clock):
    from app.agents.factory import build_agent_deps
    from app.container import build_container
    from app.providers.mock import MockLLMProvider, MockVisionProvider
    from app.rag.knowledge import sync_corpus

    deps = build_agent_deps(api_settings, llm_provider=MockLLMProvider(), vision_provider=MockVisionProvider())
    c = await build_container(api_settings, engine=engine, clock=clock, deps=deps)
    async with c.session_factory() as s:
        await sync_corpus(s, deps.embedder, api_settings.knowledge_dir)
        await s.commit()
    return c


@pytest.fixture
async def client(api_settings, container):
    from app.main import create_app

    app = create_app(api_settings, container=container)
    app.state.container = container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
