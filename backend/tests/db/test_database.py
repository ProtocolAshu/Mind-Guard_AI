import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.database.models import AuditLog, Event, Intervention, Memory
from app.memory.service import MemoryService
from app.providers.embeddings import HashingEmbeddingProvider
from app.schemas.common import MemoryType
from app.security.audit import append_audit, verify_audit_chain
from tests.conftest import IS_PG, seed_user

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def test_check_constraints_reject_invalid_enum_values(session):
    uid = await seed_user(session)
    session.add(Intervention(user_id=uid, proposed_decision="NUKE_PHONE", final_decision="ALLOW", confidence=0.5, decided_by="llm"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_event_idempotency_unique_constraint(session):
    uid = await seed_user(session)
    for _ in range(2):
        session.add(Event(user_id=uid, client_event_id="same-id-000001", event_type="APP_OPENED", occurred_at=NOW, payload={}))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_audit_chain_detects_tampering_and_deletion(session):
    for i in range(5):
        await append_audit(session, actor="system", action=f"test.{i}", resource_type="test", details={"i": i, "score": 0.1 * i},
                           now=NOW + timedelta(seconds=i))
    await session.commit()
    assert (await verify_audit_chain(session)).valid
    await session.execute(update(AuditLog).where(AuditLog.seq == 3).values(details={"i": 999}))
    await session.commit()
    broken = await verify_audit_chain(session)
    assert not broken.valid and broken.first_invalid_seq == 3 and "modified" in broken.reason


async def test_memory_retrieval_ranking_user_isolation_and_preference_supersession(session):
    embedder = HashingEmbeddingProvider()
    a = await seed_user(session)
    b = await seed_user(session)
    svc = MemoryService(session, embedder)
    await svc.add(a, MemoryType.EPISODIC, "Instagram late night session: DELAY -> accepted", now=NOW - timedelta(days=2),
                  meta={"decision": "DELAY", "reward": 1.0})
    await svc.add(a, MemoryType.EPISODIC, "YouTube morning lecture allowed", now=NOW - timedelta(days=40))
    await svc.add(b, MemoryType.EPISODIC, "Instagram late night session: BLOCK -> overridden", now=NOW)
    await session.commit()
    hits = await svc.retrieve(a, "instagram late night", now=NOW, k=5)
    assert hits[0].content.startswith("Instagram late night session: DELAY")
    assert all("BLOCK" not in h.content for h in hits)  # never another user's memory
    assert hits[0].is_recent and not hits[-1].is_recent
    first = await svc.upsert_preference(a, "intervention_preference", "prefers DELAY", now=NOW, importance=0.8, meta={"preferred": "DELAY"})
    second = await svc.upsert_preference(a, "intervention_preference", "prefers SOFT_WARNING", now=NOW + timedelta(minutes=1),
                                         importance=0.8, meta={"preferred": "SOFT_WARNING"})
    await session.commit()
    current = await svc.current_preference(a, "intervention_preference", NOW + timedelta(minutes=2))
    assert current.id == second.id
    old = await session.get(Memory, first.id)
    assert old.expires_at is not None


@pytest.mark.skipif(not IS_PG, reason="pgvector index plan is PostgreSQL-specific")
async def test_pgvector_hnsw_index_is_used(session):
    uid = await seed_user(session)
    svc = MemoryService(session, HashingEmbeddingProvider())
    for i in range(30):
        await svc.add(uid, MemoryType.EPISODIC, f"episode {i} instagram session outcome", now=NOW)
    await session.commit()
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    vec = "[" + ",".join(["0.05"] * 384) + "]"
    plan = (await session.execute(text(
        "EXPLAIN SELECT id FROM embeddings WHERE dim = 384 ORDER BY (embedding::vector(384)) <=> CAST(:q AS vector(384)) LIMIT 5"),
        {"q": vec})).scalars().all()
    assert any("ix_embeddings_hnsw_384" in line for line in plan), plan


async def test_user_deletion_cascades(session):
    from app.database.models import User

    uid = await seed_user(session)
    session.add(Event(user_id=uid, client_event_id=f"e-{uuid.uuid4().hex[:12]}", event_type="APP_OPENED", occurred_at=NOW, payload={}))
    await session.commit()
    await session.delete(await session.get(User, uid))
    await session.commit()
    assert (await session.execute(select(Event).where(Event.user_id == uid))).first() is None


async def test_model_version_column_fits_the_served_registry(session):
    """Regression: the comma-joined registry version overflowed VARCHAR(64) on PostgreSQL (SQLite never enforced it)."""
    from app.core.config import Settings
    from app.database.models import RiskScore
    from app.risk.scoring import RiskModelRegistry

    column_length = RiskScore.__table__.c.model_version.type.length
    version = RiskModelRegistry(Settings(environment="test", jwt_secret="t" * 48).model_dir).version_string()
    if version is None:
        pytest.skip("no trained models available")
    assert len(version) <= column_length, f"registry version is {len(version)} chars, column holds {column_length}"
