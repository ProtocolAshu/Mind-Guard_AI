from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.content_agent import MAX_IMAGE_BYTES, analyze_screenshot
from app.api.deps import get_container, get_current_user, get_now, get_session, rate_limit
from app.container import Container
from app.core.errors import ConsentRequiredError, ValidationFailedError, require
from app.database.models import ContentSignal, Intervention, InterventionOutcome, RiskScore, User
from app.schemas.agents import ContentAssessment
from app.schemas.api import RiskAssessRequest
from app.schemas.common import ContentSource, RunStatus, RunType
from app.schemas.evaluation import EvaluateRequest, EvaluationResponse, FeedbackRequest, FeedbackResponse, RiskSummary
from app.services.goals import active_goals
from app.services.interventions import get_intervention

router = APIRouter(tags=["Interventions", "Feedback", "Risk", "Content"])
LLM_LIMIT = Depends(rate_limit("llm", "rate_limit_llm_per_minute"))


@router.post("/api/interventions/evaluate", response_model=EvaluationResponse, dependencies=[LLM_LIMIT])
async def evaluate(body: EvaluateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                   container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> EvaluationResponse:
    return await container.orchestrator.evaluate(session, user.id, body, now)


@router.post("/api/interventions/feedback", response_model=FeedbackResponse)
async def feedback(body: FeedbackRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                   container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> FeedbackResponse:
    return await container.orchestrator.feedback(session, user.id, body, now)


def _intervention(i: Intervention, outcome: InterventionOutcome | None) -> dict[str, object]:
    command = {k: v for k, v in (i.command or {}).items() if k != "ticket_nonce"}
    return {"id": str(i.id), "run_id": str(i.run_id) if i.run_id else None, "app_package": i.app_package,
            "proposed_decision": i.proposed_decision, "final_decision": i.final_decision, "duration_minutes": i.duration_minutes,
            "confidence": i.confidence, "reason_codes": i.reason_codes, "guardrail_flags": i.guardrail_flags,
            "explanation": i.explanation, "decided_by": i.decided_by, "status": i.status, "command": command,
            "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            "outcome": {"outcome": outcome.outcome, "reward": outcome.reward, "satisfaction": outcome.satisfaction} if outcome else None}


@router.get("/api/interventions")
async def list_interventions(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
                             user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[dict[str, object]]:
    rows = (await session.execute(select(Intervention, InterventionOutcome)
                                  .outerjoin(InterventionOutcome, InterventionOutcome.intervention_id == Intervention.id)
                                  .where(Intervention.user_id == user.id).order_by(Intervention.created_at.desc())
                                  .limit(limit).offset(offset))).all()
    return [_intervention(i, o) for i, o in rows]


@router.get("/api/interventions/{intervention_id}")
async def read_intervention(intervention_id: uuid.UUID, user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    i = await get_intervention(session, user.id, intervention_id)
    outcome = (await session.execute(select(InterventionOutcome).where(InterventionOutcome.intervention_id == i.id))).scalar_one_or_none()
    return _intervention(i, outcome)


@router.get("/api/risk/history")
async def risk_history(days: int = Query(default=7, ge=1, le=90), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> list[dict[str, object]]:
    rows = (await session.execute(select(RiskScore).where(RiskScore.user_id == user.id, RiskScore.created_at >= now - timedelta(days=days))
                                  .order_by(RiskScore.created_at.desc()).limit(2000))).scalars()
    return [{"created_at": r.created_at.isoformat(), "distraction_risk": r.distraction_risk, "doomscroll_probability": r.doomscroll_probability,
             "goal_conflict": r.goal_conflict, "confidence": r.confidence, "method": r.method, "model_version": r.model_version}
            for r in rows]


@router.post("/api/risk/assess")
async def risk_assess(body: RiskAssessRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> dict[str, object]:
    """Dry run: context -> behaviour -> policy -> risk. Nothing is persisted and no intervention is created."""
    ctx = await container.orchestrator.context_for(session, user.id, now)
    ctx.recorder = None
    tools = container.deps.tools
    context = await tools.invoke("get_current_context", {"app_package": body.app_package, "session_minutes_hint": body.session_minutes}, ctx)
    if not context.ok:
        raise ConsentRequiredError(context.error or "usage monitoring consent is required", code="consent_required")
    behavior = await tools.invoke("get_behavior_profile", {"days": 14}, ctx)
    policy = await tools.invoke("evaluate_policy", {"context": context.output.model_dump(mode="json")}, ctx)  # type: ignore[union-attr]
    risk = await tools.invoke("calculate_risk", {"context": context.output.model_dump(mode="json"),  # type: ignore[union-attr]
                                                 "behavior": behavior.output.model_dump(mode="json"),  # type: ignore[union-attr]
                                                 "verdict": policy.output.verdict.model_dump(mode="json")}, ctx)  # type: ignore[union-attr]
    return {"risk": RiskSummary.of(risk.output).model_dump(mode="json"),  # type: ignore[arg-type]
            "policy": policy.output.verdict.model_dump(mode="json")}  # type: ignore[union-attr]


async def _content_run(container: Container, session: AsyncSession, user: User, now: datetime, source: ContentSource,
                       text: str | None, image: bytes | None, app_package: str | None) -> ContentAssessment:
    ctx = await container.orchestrator.context_for(session, user.id, now)
    recorder = require(ctx.recorder, "recorder missing")
    ctx.run_id = await recorder.start_run(user_id=user.id, run_type=RunType.CONTENT_ANALYSIS,
                                          trigger={"source": source.value, "app_package": app_package}, now=now)
    status, error = RunStatus.SUCCEEDED, None
    try:
        if image is not None:
            goals = await active_goals(session, user.id, now)
            try:
                assessment = await analyze_screenshot(ctx, image=image, goals=goals)
            except PermissionError as exc:
                raise ConsentRequiredError(str(exc), code="consent_required") from exc
            except ValueError as exc:
                raise ValidationFailedError(str(exc), code="invalid_image") from exc
        else:
            result = await container.deps.tools.invoke("classify_content", {"text": text, "source": source.value,
                                                                            "app_package": app_package}, ctx)
            if not result.ok:
                raise ValidationFailedError(result.error or "content analysis failed")
            assessment = result.output  # type: ignore[assignment]
        session.add(ContentSignal(user_id=user.id, source=assessment.source.value, category=assessment.category.value,
                                  goal_relevance=assessment.goal_relevance.value, confidence=assessment.confidence,
                                  classifier=assessment.classifier, injection_detected=assessment.injection_detected,
                                  injection_score=assessment.injection_score, text_sha256=assessment.text_sha256, created_at=now))
        return assessment
    except Exception as exc:
        status, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
        raise
    finally:
        await recorder.finish_run(require(ctx.run_id, "run id missing"), run_type=RunType.CONTENT_ANALYSIS, status=status,
                                  path=["content"], final_decision=None, error=error, latency_ms=0.0, now=now)


@router.post("/api/content/analyze", response_model=ContentAssessment, dependencies=[LLM_LIMIT])
async def analyze_content(body: dict[str, object], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                          container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> ContentAssessment:
    from app.schemas.evaluation import ContentInput

    parsed = ContentInput.model_validate({k: v for k, v in body.items() if k in ("text", "source")})
    app_package = body.get("app_package") if isinstance(body.get("app_package"), str) else None
    return await _content_run(container, session, user, now, parsed.source, parsed.text, None, app_package)  # type: ignore[arg-type]


@router.post("/api/content/screenshot", response_model=ContentAssessment, dependencies=[LLM_LIMIT])
async def analyze_image(file: UploadFile = File(...), app_package: str | None = Form(default=None),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> ContentAssessment:
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationFailedError("image too large", code="invalid_image")
    return await _content_run(container, session, user, now, ContentSource.SCREENSHOT, None, data, app_package)

