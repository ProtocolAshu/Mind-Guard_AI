"""Seeds synthetic users for the admin console's debug/replay mode (section 34).

Each simulator persona becomes a user `<persona>@synthetic.mindguard.dev` (unusable password: nobody can sign in as
them). Their constitution is compiled and saved, a few simulated days of usage events are ingested, and the real
agent graph evaluates their sessions; persona-driven feedback closes the learning loop. Cloud AI consent is off, so
seeding makes no LLM calls. All data is synthetic and labelled as such.

    PYTHONPATH=backend:. python scripts/seed_synthetic.py --days 3             # uses DATABASE_URL / .env
    PYTHONPATH=backend:. python scripts/seed_synthetic.py --reset --days 5
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.agents.factory import build_agent_deps
from app.agents.supervisor import Orchestrator
from app.core.config import Settings
from app.database.models import AgentRun, Consent, Intervention, User, UserPreference
from app.database.session import create_all, create_engine, create_session_factory
from app.schemas.common import CompiledBy, OutcomeType
from app.schemas.common import InterventionType as IT
from app.schemas.evaluation import ConstitutionRequest, EvaluateRequest, FeedbackRequest
from app.services.policies import save_constitution
from app.services.privacy import delete_user_data
from app.services.usage import ClientEvent, ingest_event
from simulator.dataset import simulate_persona
from simulator.personas import PERSONAS, Persona

DOMAIN = "synthetic.mindguard.dev"
SIM_BASE = datetime(2026, 1, 5, tzinfo=UTC)  # simulator day 0 (a Monday)
RESTRICTIVE = {IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK}


def week_aligned_offset(days: int, now: datetime) -> timedelta:
    """Shift simulated history by whole weeks so it ends before `now` and weekday patterns are preserved."""
    end = SIM_BASE + timedelta(days=days)
    weeks = max(0, (now - end).days // 7)
    return timedelta(weeks=weeks)


def with_checkpoints(events: list[dict[str, Any]], every_s: int = 300) -> list[dict[str, Any]]:
    """The simulator emits APP_OPENED/APP_CLOSED pairs; the Android SessionTracker additionally sends SESSION_EXTENDED
    every five minutes of continuous use. Recreate those checkpoints so sessions can be evaluated while they run."""
    out = list(events)
    for e in events:
        if e["event_type"] != "APP_CLOSED" or not e["app_package"]:
            continue
        duration = int(e["payload"].get("duration_seconds", 0))
        scroll = int(e["payload"].get("scroll_events", 0))
        closed = datetime.fromisoformat(e["occurred_at"])
        opened = closed - timedelta(seconds=duration)
        for k in range(1, duration // every_s + 1):
            elapsed = k * every_s
            if elapsed >= duration:
                break
            out.append({"client_event_id": f"{e['client_event_id']}-ext{k}", "event_type": "SESSION_EXTENDED",
                        "occurred_at": (opened + timedelta(seconds=elapsed)).isoformat(), "app_package": e["app_package"],
                        "payload": {"duration_seconds": elapsed, "scroll_events": round(scroll * elapsed / max(duration, 1))}})
    return sorted(out, key=lambda ev: ev["occurred_at"])


def outcome_for(persona: Persona, decision: IT, key: str) -> OutcomeType:
    rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))
    accept = min(0.95, max(0.05, persona.commitment * persona.affinity.get(decision, 1.0)))
    if decision in RESTRICTIVE and rng.random() < persona.reactance * 0.6:
        return OutcomeType.OVERRIDDEN
    return OutcomeType.ACCEPTED if rng.random() < accept else OutcomeType.IGNORED


async def seed_persona(orch: Orchestrator, factory: Any, persona: Persona, *, seed: int, days: int, per_day: int, reset: bool) -> dict[str, int]:
    email = f"{persona.name}@{DOMAIN}"
    now = datetime.now(UTC)
    async with factory() as session:
        existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing and not reset:
            return {"skipped": 1}
        if existing:
            await delete_user_data(session, existing.id, scope="all", actor="seed_synthetic", now=now)
            await session.commit()
        user = User(email=email, password_hash="!", display_name=persona.name.replace("_", " ").title())
        session.add(user)
        await session.flush()
        session.add(UserPreference(user_id=user.id, timezone="UTC", intervention_style=persona.style, content_analysis_enabled=True))
        for scope in ("usage_monitoring", "content_text_analysis", "memory_personalization"):
            session.add(Consent(user_id=user.id, scope=scope, granted=True))
        await session.commit()
        uid = user.id

        simulated = simulate_persona(persona, seed=seed, days=days)["events"]
        shift = week_aligned_offset(days, now)
        events = with_checkpoints(simulated)
        first_at = datetime.fromisoformat(events[0]["occurred_at"]) + shift if events else now
        if persona.constitution:
            preview = await orch.compile_constitution(session, uid, ConstitutionRequest(text=persona.constitution, allow_llm=False), first_at)
            if preview.constitution.rules:
                await save_constitution(session, uid, constitution=preview.constitution, source_text=persona.constitution,
                                        compiled_by=CompiledBy.DETERMINISTIC, actor="seed_synthetic", now=first_at)
            await session.commit()

        counts = {"events": 0, "evaluations": 0, "interventions": 0}
        evaluations_by_day: dict[str, int] = {}
        for raw in events:
            at = datetime.fromisoformat(raw["occurred_at"]) + shift
            event = ClientEvent(client_event_id=raw["client_event_id"], event_type=raw["event_type"], occurred_at=at,
                                app_package=raw["app_package"], payload=raw["payload"])
            result, _ = await ingest_event(session, uid, event, now=at, max_skew_s=900, max_age_days=days + 1)
            counts["events"] += result == "accepted"
            day = at.date().isoformat()
            if raw["event_type"] != "SESSION_EXTENDED" or not raw["app_package"] or evaluations_by_day.get(day, 0) >= per_day:
                continue
            await session.commit()
            evaluations_by_day[day] = evaluations_by_day.get(day, 0) + 1
            response = await orch.evaluate(session, uid, EvaluateRequest(app_package=raw["app_package"]), at + timedelta(seconds=1))
            await session.commit()
            counts["evaluations"] += 1
            if response.intervention_id is not None:
                counts["interventions"] += 1
                outcome = outcome_for(persona, response.decision, f"{persona.name}:{seed}:{raw['client_event_id']}")
                await orch.feedback(session, uid, FeedbackRequest(intervention_id=response.intervention_id, outcome=outcome),
                                    at + timedelta(minutes=1))
                await session.commit()
        await session.commit()
        return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL from the environment/.env")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--evaluations-per-day", type=int, default=12)
    parser.add_argument("--reset", action="store_true", help="delete and recreate existing synthetic users")
    args = parser.parse_args()
    settings = Settings() if args.database_url is None else Settings(database_url=args.database_url)
    engine = create_engine(settings.database_url)
    if engine.dialect.name == "sqlite":
        await create_all(engine)  # development convenience; PostgreSQL uses `alembic upgrade head`
    factory = create_session_factory(engine)
    orch = Orchestrator(build_agent_deps(settings, llm_provider=None, vision_provider=None), factory)
    for persona in PERSONAS:
        counts = await seed_persona(orch, factory, persona, seed=args.seed, days=args.days, per_day=args.evaluations_per_day, reset=args.reset)
        print(f"{persona.name:24s} {counts}")
    async with factory() as session:
        users = (await session.execute(select(func.count()).select_from(User).where(User.email.like(f"%@{DOMAIN}")))).scalar_one()
        runs = (await session.execute(select(func.count()).select_from(AgentRun).join(User, AgentRun.user_id == User.id)
                                      .where(User.email.like(f"%@{DOMAIN}")))).scalar_one()
        interventions = (await session.execute(select(func.count()).select_from(Intervention).join(User, Intervention.user_id == User.id)
                                               .where(User.email.like(f"%@{DOMAIN}")))).scalar_one()
    print(f"synthetic users: {users}, agent runs: {runs}, interventions: {interventions}  (id {uuid.uuid4().hex[:6]})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
