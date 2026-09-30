"""Alembic environment (async). PostgreSQL is the migration target; SQLite development
databases are bootstrapped with `create_all` instead."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

import sqlalchemy as sa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.database import models  # noqa: F401  (registers tables on Base.metadata)
from app.database.base import Base, EmbeddingVector, UTCDateTime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def database_url() -> str:
    from app.database.session import normalize_database_url

    return normalize_database_url(config.get_main_option("sqlalchemy.url") or get_settings().database_url)


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render project TypeDecorators as their PostgreSQL implementations."""
    if type_ != "type":
        return False
    if isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    if isinstance(obj, EmbeddingVector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return "pgvector.sqlalchemy.Vector()"
    if isinstance(obj, sa.JSON) and "postgresql" in getattr(obj, "_variant_mapping", {}):
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.JSONB(astext_type=sa.Text())"
    return False


def _configure(**kwargs: Any) -> None:
    context.configure(target_metadata=target_metadata, render_item=render_item, compare_type=True, **kwargs)


def run_offline() -> None:
    _configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Any) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
