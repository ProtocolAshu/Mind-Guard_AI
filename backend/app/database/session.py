"""Async engine / session factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base

log = logging.getLogger(__name__)

# libpq-only query parameters that asyncpg rejects; managed providers put them in the URL they hand out.
_LIBPQ_ONLY = ("channel_binding", "target_session_attrs", "connect_timeout", "application_name", "options")


def normalize_database_url(url: str) -> str:
    """Accept the connection strings managed PostgreSQL providers give you.

    `postgres://` and `postgresql://` become the asyncpg driver URL, libpq's `sslmode=` becomes asyncpg's `ssl=`,
    and libpq-only parameters are dropped. SQLite URLs pass through untouched.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if "+asyncpg" not in url or "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept: list[str] = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        if key == "sslmode":
            kept.append(f"ssl={value}")
        elif key in _LIBPQ_ONLY:
            log.info("dropping libpq-only connection parameter %r (unsupported by asyncpg)", key)
        else:
            kept.append(part)
    return base + ("?" + "&".join(kept) if kept else "")


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    url = normalize_database_url(url)
    kwargs: dict[str, Any] = {"echo": echo}
    if url.startswith("sqlite"):
        if ":memory:" in url or url.endswith("sqlite+aiosqlite://"):
            kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
    engine = create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            # Let SQLAlchemy own transaction boundaries so SAVEPOINTs work (documented pysqlite recipe).
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        @event.listens_for(engine.sync_engine, "begin")
        def _sqlite_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def create_all(engine: AsyncEngine) -> None:
    """Development/test bootstrap. Production uses Alembic migrations."""
    from app.database import models  # noqa: F401  (register every table on Base.metadata)

    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.create_all)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
