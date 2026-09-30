"""Memory system (section 5) with the priority rules of section 45.

Types: semantic (goals/preferences the user stated), episodic (intervention episodes:
context summary -> action -> outcome -> reward), preference (learned, keyed, newest
wins), insight (analytics summaries). Raw third-party content is never stored.

Retrieval = vector similarity (pgvector HNSW on PostgreSQL, exact cosine elsewhere)
+ importance + recency. Memories are *inputs*: they can inform a choice inside the
policy-bounded action set but can never override a policy or current instruction.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import ensure_utc
from app.core.errors import NotFoundError, ValidationFailedError
from app.database.models import Embedding, Memory
from app.providers.base import EmbeddingProvider
from app.providers.embeddings import cosine
from app.schemas.agents import MemoryHit
from app.schemas.common import MemorySource, MemoryType
from app.security.injection import detect_injection, sanitize_untrusted_text

log = logging.getLogger(__name__)
RECENT_DAYS = 14
MAX_MEMORY_CHARS = 1000
SQLITE_SCAN_LIMIT = 2000


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


class MemoryService:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider):
        self.session = session
        self.embedder = embedder

    async def add(
        self,
        user_id: uuid.UUID,
        memory_type: MemoryType,
        content: str,
        *,
        now: datetime,
        importance: float = 0.5,
        source: MemorySource = MemorySource.SYSTEM,
        meta: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> Memory:
        clean = sanitize_untrusted_text(content, max_chars=MAX_MEMORY_CHARS)
        if not clean:
            raise ValidationFailedError("memory content is empty")
        if detect_injection(clean).detected:
            raise ValidationFailedError("memory content looks like instructions and was rejected", code="memory_rejected")
        memory = Memory(user_id=user_id, memory_type=memory_type.value, content=clean, meta=meta or {},
                        importance=max(0.0, min(1.0, importance)), source=source.value, created_at=now, expires_at=expires_at)
        self.session.add(memory)
        await self.session.flush()
        vector = (await self.embedder.embed([clean]))[0]
        self.session.add(Embedding(owner_type="memory", owner_id=memory.id, user_id=user_id, model=self.embedder.model_name,
                                   dim=len(vector), embedding=vector, created_at=now))
        await self.session.flush()
        return memory

    async def upsert_preference(self, user_id: uuid.UUID, key: str, content: str, *, now: datetime, importance: float,
                                meta: dict[str, Any]) -> Memory:
        """Newest preference for a key supersedes older ones (recent memory > older memory)."""
        await self.session.execute(
            update(Memory)
            .where(Memory.user_id == user_id, Memory.memory_type == MemoryType.PREFERENCE.value, Memory.expires_at.is_(None),
                   Memory.meta["key"].as_string() == key)
            .values(expires_at=now)
        )
        return await self.add(user_id, MemoryType.PREFERENCE, content, now=now, importance=importance,
                              source=MemorySource.LEARNING, meta={**meta, "key": key})

    async def current_preference(self, user_id: uuid.UUID, key: str, now: datetime) -> Memory | None:
        rows = (
            await self.session.execute(
                select(Memory)
                .where(Memory.user_id == user_id, Memory.memory_type == MemoryType.PREFERENCE.value,
                       Memory.meta["key"].as_string() == key)
                .order_by(Memory.created_at.desc())
                .limit(5)
            )
        ).scalars()
        for row in rows:
            if row.expires_at is None or ensure_utc(row.expires_at) > now:
                return row
        return None

    async def retrieve(
        self, user_id: uuid.UUID, query: str, *, now: datetime, k: int = 5, types: list[MemoryType] | None = None
    ) -> list[MemoryHit]:
        k = max(1, min(k, 20))
        vector = (await self.embedder.embed([sanitize_untrusted_text(query, 500)]))[0]
        type_values = [t.value for t in types] if types else None
        if self.session.get_bind().dialect.name == "postgresql":
            candidates = await self._pg_candidates(user_id, vector, now, k * 4, type_values)
        else:
            candidates = await self._scan_candidates(user_id, vector, now, type_values)
        hits: list[MemoryHit] = []
        for memory, similarity in candidates:
            age_days = max(0.0, (now - ensure_utc(memory.created_at)).total_seconds() / 86_400)
            recency = math.exp(-age_days / 30.0)
            score = 0.6 * similarity + 0.25 * memory.importance + 0.15 * recency
            hits.append(MemoryHit(id=memory.id, memory_type=MemoryType(memory.memory_type), content=memory.content,
                                  importance=memory.importance, similarity=round(similarity, 4), score=round(score, 4),
                                  created_at=memory.created_at, is_recent=age_days <= RECENT_DAYS, meta=memory.meta or {}))
        hits.sort(key=lambda h: (-h.score, not h.is_recent))
        top = hits[:k]
        if top:
            await self.session.execute(update(Memory).where(Memory.id.in_([h.id for h in top])).values(last_accessed_at=now))
        return top

    async def _pg_candidates(self, user_id: uuid.UUID, vector: list[float], now: datetime, limit: int,
                             types: list[str] | None) -> list[tuple[Memory, float]]:
        dim = len(vector)
        type_clause = "AND m.memory_type = ANY(:types)" if types else ""
        sql = text(
            f"""
            SELECT m.id, 1 - ((e.embedding::vector({dim})) <=> CAST(:q AS vector({dim}))) AS similarity
            FROM embeddings e JOIN memories m ON m.id = e.owner_id
            WHERE e.owner_type = 'memory' AND e.user_id = :uid AND e.dim = :dim AND e.model = :model
              AND (m.expires_at IS NULL OR m.expires_at > :now) {type_clause}
            ORDER BY (e.embedding::vector({dim})) <=> CAST(:q AS vector({dim}))
            LIMIT :limit
            """  # noqa: S608  # nosec B608 - dim is an int derived from the embedder, never user input
        )
        params: dict[str, Any] = {"q": _vector_literal(vector), "uid": user_id, "dim": dim,
                                  "model": self.embedder.model_name, "now": now, "limit": limit}
        if types:
            params["types"] = types
        rows = (await self.session.execute(sql, params)).all()
        if not rows:
            return []
        memories = {m.id: m for m in (await self.session.execute(select(Memory).where(Memory.id.in_([r.id for r in rows])))).scalars()}
        return [(memories[r.id], float(r.similarity)) for r in rows if r.id in memories]

    async def _scan_candidates(self, user_id: uuid.UUID, vector: list[float], now: datetime,
                               types: list[str] | None) -> list[tuple[Memory, float]]:
        stmt = (
            select(Memory, Embedding.embedding)
            .join(Embedding, (Embedding.owner_id == Memory.id) & (Embedding.owner_type == "memory"))
            .where(Memory.user_id == user_id, Embedding.model == self.embedder.model_name)
            .order_by(Memory.created_at.desc())
            .limit(SQLITE_SCAN_LIMIT)
        )
        if types:
            stmt = stmt.where(Memory.memory_type.in_(types))
        out: list[tuple[Memory, float]] = []
        for memory, emb in (await self.session.execute(stmt)).all():
            if memory.expires_at is not None and ensure_utc(memory.expires_at) <= now:
                continue
            out.append((memory, cosine(vector, emb)))
        return out

    async def list(self, user_id: uuid.UUID, *, memory_type: MemoryType | None, limit: int, offset: int,
                   include_expired: bool = False, now: datetime) -> list[Memory]:
        stmt = select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc()).limit(limit).offset(offset)
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.value)
        rows = list((await self.session.execute(stmt)).scalars())
        if include_expired:
            return rows
        return [m for m in rows if m.expires_at is None or ensure_utc(m.expires_at) > now]

    async def delete(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> None:
        memory = (await self.session.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))).scalar_one_or_none()
        if memory is None:
            raise NotFoundError("memory not found")
        await self.session.execute(delete(Embedding).where(Embedding.owner_type == "memory", Embedding.owner_id == memory_id))
        await self.session.delete(memory)
        await self.session.flush()

    async def purge_expired(self, user_id: uuid.UUID, now: datetime, older_than_days: int = 30) -> int:
        cutoff = now - timedelta(days=older_than_days)
        ids = [m for m in (await self.session.execute(select(Memory.id).where(
            Memory.user_id == user_id, Memory.expires_at.is_not(None), Memory.expires_at < cutoff))).scalars()]
        if ids:
            await self.session.execute(delete(Embedding).where(Embedding.owner_type == "memory", Embedding.owner_id.in_(ids)))
            await self.session.execute(delete(Memory).where(Memory.id.in_(ids)))
        return len(ids)
