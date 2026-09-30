"""Knowledge retrieval over versioned documentation (section 6, use cases 4-6).

Why retrieval: the Goal Agent must ground "unsupported"/capability explanations in
what the Android platform and MindGuard actually allow, and the dashboard's help
panel answers "why did this happen / what can I do" from the same source. Stuffing
every document into every prompt would cost tokens and dilute relevance; retrieving
the 2-3 most relevant chunks keeps prompts small and answers grounded.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Embedding, KnowledgeDocument
from app.providers.base import EmbeddingProvider
from app.providers.embeddings import cosine

CHUNK_CHARS = 900


@dataclass(frozen=True)
class KnowledgeHit:
    slug: str
    title: str
    chunk_index: int
    content: str
    similarity: float
    kind: str


def chunk_markdown(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    sections = re.split(r"\n(?=#{1,3} )", text.strip())
    chunks: list[str] = []
    for section in sections:
        buffer = ""
        for paragraph in re.split(r"\n\s*\n", section):
            if len(buffer) + len(paragraph) + 2 > max_chars and buffer:
                chunks.append(buffer.strip())
                buffer = ""
            buffer += paragraph + "\n\n"
        if buffer.strip():
            chunks.append(buffer.strip())
    return chunks


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        return meta, body
    return meta, text


async def sync_corpus(session: AsyncSession, embedder: EmbeddingProvider, corpus_dir: Path) -> int:
    """Idempotently (re)index the corpus; a document is re-embedded only when its hash changes."""
    indexed = 0
    paths = await asyncio.to_thread(lambda: sorted(Path(corpus_dir).glob("*.md")))
    for path in paths:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        meta, body = _front_matter(raw)
        slug = path.stem
        existing = list((await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))).scalars())
        if existing and all((d.meta or {}).get("sha256") == digest for d in existing):
            continue
        ids = [d.id for d in existing]
        if ids:
            await session.execute(delete(Embedding).where(Embedding.owner_type == "knowledge", Embedding.owner_id.in_(ids)))
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(ids)))
        chunks = chunk_markdown(body)
        vectors = await embedder.embed(chunks)
        title = meta.get("title", slug.replace("-", " ").title())
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            doc = KnowledgeDocument(slug=slug, title=title, chunk_index=i, content=chunk,
                                    meta={"sha256": digest, "kind": meta.get("kind", "system")})
            session.add(doc)
            await session.flush()
            session.add(Embedding(owner_type="knowledge", owner_id=doc.id, user_id=None, model=embedder.model_name,
                                  dim=len(vector), embedding=vector))
        indexed += 1
    await session.flush()
    return indexed


async def search_knowledge(session: AsyncSession, embedder: EmbeddingProvider, query: str, *, k: int = 3,
                           kind: str | None = None) -> list[KnowledgeHit]:
    vector = (await embedder.embed([query[:500]]))[0]
    rows = (
        await session.execute(
            select(KnowledgeDocument, Embedding.embedding)
            .join(Embedding, (Embedding.owner_id == KnowledgeDocument.id) & (Embedding.owner_type == "knowledge"))
            .where(Embedding.model == embedder.model_name)
        )
    ).all()
    hits = [
        KnowledgeHit(doc.slug, doc.title, doc.chunk_index, doc.content, round(cosine(vector, emb), 4), (doc.meta or {}).get("kind", "system"))
        for doc, emb in rows
        if kind is None or (doc.meta or {}).get("kind") == kind
    ]
    hits.sort(key=lambda h: -h.similarity)
    return hits[: max(1, min(k, 10))]
