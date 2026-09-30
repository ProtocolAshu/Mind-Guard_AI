from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_current_user, get_now, get_session, rate_limit
from app.container import Container
from app.core.errors import NotFoundError, ValidationFailedError
from app.database.models import Memory, Policy, PolicyOverride, PolicyVersion, User
from app.policies.catalog import APPS
from app.policies.engine import explain_rule, validate_rules
from app.schemas.api import (
    OverrideCreate,
    OverrideOut,
    PolicyFromConstitution,
    PolicyOut,
    PolicyRulesUpdate,
    PolicyStatusUpdate,
    PolicyVersionOut,
    ValidateRulesRequest,
)
from app.schemas.common import AppCategory, CompiledBy, ContentCategory, MemoryType, PolicyEffect, PolicyStatus
from app.schemas.evaluation import ConstitutionPreviewResponse, ConstitutionRequest
from app.schemas.policy import Escalation, PolicyRule, ValidationReport
from app.services.policies import (
    active_overrides,
    create_override,
    current_version,
    get_policy,
    revoke_override,
    save_constitution,
    set_policy_status,
    update_policy,
)
from app.services.users import get_preferences, user_timezone

router = APIRouter(tags=["Policies"])
LLM_LIMIT = Depends(rate_limit("llm", "rate_limit_llm_per_minute"))


async def _policy_out(session: AsyncSession, policy: Policy) -> PolicyOut:
    version = await current_version(session, policy)
    rules = [PolicyRule.model_validate(r) for r in version.rules]
    return PolicyOut(id=policy.id, name=policy.name, status=PolicyStatus(policy.status), goal_id=policy.goal_id,
                     current_version=policy.current_version, compiled_by=CompiledBy(version.compiled_by),
                     source_text=version.source_text, rules=rules, rule_explanations={r.rule_id: explain_rule(r) for r in rules},
                     validation=validate_rules(rules), created_at=policy.created_at, updated_at=policy.updated_at)


@router.get("/api/policies/catalog")
async def catalog() -> dict[str, object]:
    return {"apps": [{"package": a.package, "name": a.name, "category": a.category.value, "essential": a.category is AppCategory.ESSENTIAL}
                     for a in APPS],
            "app_categories": [c.value for c in AppCategory], "content_categories": [c.value for c in ContentCategory],
            "effects": [e.value for e in PolicyEffect], "escalations": [e.value for e in Escalation]}


@router.post("/api/policies/compile", response_model=ConstitutionPreviewResponse, dependencies=[LLM_LIMIT])
async def compile_preview(body: ConstitutionRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                          container: Container = Depends(get_container), now: datetime = Depends(get_now)) -> ConstitutionPreviewResponse:
    return await container.orchestrator.compile_constitution(session, user.id, body, now)


@router.post("/api/policies/validate", response_model=ValidationReport)
async def validate(body: ValidateRulesRequest, _: User = Depends(get_current_user)) -> ValidationReport:
    return validate_rules(body.rules)


@router.post("/api/policies", status_code=201, dependencies=[LLM_LIMIT])
async def create_from_constitution(body: PolicyFromConstitution, user: User = Depends(get_current_user),
                                   session: AsyncSession = Depends(get_session), container: Container = Depends(get_container),
                                   now: datetime = Depends(get_now)) -> dict[str, object]:
    preview = await container.orchestrator.compile_constitution(session, user.id, ConstitutionRequest(text=body.text,
                                                                                                    allow_llm=body.allow_llm), now)
    if not preview.constitution.rules:
        raise ValidationFailedError("no enforceable rules were found", code="no_rules",
                                    details={"unsupported": [u.model_dump() for u in preview.constitution.unsupported],
                                             "clarifications": preview.constitution.clarifications})
    policy, _, goal = await save_constitution(session, user.id, constitution=preview.constitution, source_text=body.text,
                                              compiled_by=preview.compiled_by, actor=f"user:{user.id}", now=now, name=body.name)
    return {"policy": (await _policy_out(session, policy)).model_dump(mode="json"), "goal_id": str(goal.id) if goal else None,
            "preview": preview.model_dump(mode="json")}


@router.get("/api/policies", response_model=list[PolicyOut])
async def list_policies(include_archived: bool = False, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> list[PolicyOut]:
    stmt = select(Policy).where(Policy.user_id == user.id).order_by(Policy.updated_at.desc())
    if not include_archived:
        stmt = stmt.where(Policy.status != PolicyStatus.ARCHIVED.value)
    return [await _policy_out(session, p) for p in (await session.execute(stmt)).scalars()]


@router.get("/api/policies/{policy_id}", response_model=PolicyOut)
async def read_policy(policy_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> PolicyOut:
    return await _policy_out(session, await get_policy(session, user.id, policy_id))


@router.put("/api/policies/{policy_id}", response_model=PolicyOut)
async def replace_rules(policy_id: uuid.UUID, body: PolicyRulesUpdate, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> PolicyOut:
    policy, _ = await update_policy(session, user.id, policy_id, rules=body.rules, source_text=body.source_text,
                                    compiled_by=CompiledBy.MANUAL, actor=f"user:{user.id}", now=now, name=body.name)
    return await _policy_out(session, policy)


@router.get("/api/policies/{policy_id}/versions", response_model=list[PolicyVersionOut])
async def versions(policy_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[PolicyVersionOut]:
    policy = await get_policy(session, user.id, policy_id)
    rows = (await session.execute(select(PolicyVersion).where(PolicyVersion.policy_id == policy.id)
                                  .order_by(PolicyVersion.version.desc()))).scalars()
    return [PolicyVersionOut(version=v.version, compiled_by=CompiledBy(v.compiled_by), rule_count=len(v.rules),
                             source_text=v.source_text, created_at=v.created_at) for v in rows]


@router.post("/api/policies/{policy_id}/rollback/{version}", response_model=PolicyOut)
async def rollback(policy_id: uuid.UUID, version: int, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> PolicyOut:
    policy = await get_policy(session, user.id, policy_id)
    target = (await session.execute(select(PolicyVersion).where(PolicyVersion.policy_id == policy.id,
                                                                PolicyVersion.version == version))).scalar_one_or_none()
    if target is None:
        raise NotFoundError("policy version not found")
    policy, _ = await update_policy(session, user.id, policy_id, rules=[PolicyRule.model_validate(r) for r in target.rules],
                                    source_text=f"rollback to version {version}\n{target.source_text}"[:10_000],
                                    compiled_by=CompiledBy(target.compiled_by), actor=f"user:{user.id}", now=now)
    return await _policy_out(session, policy)


@router.patch("/api/policies/{policy_id}/status", response_model=PolicyOut)
async def change_status(policy_id: uuid.UUID, body: PolicyStatusUpdate, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session), now: datetime = Depends(get_now)) -> PolicyOut:
    policy = await set_policy_status(session, user.id, policy_id, body.status, actor=f"user:{user.id}", now=now)
    return await _policy_out(session, policy)


async def _suggestion(session: AsyncSession, user: User, memory_id: uuid.UUID) -> Memory:
    memory = (await session.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == user.id,
                                                         Memory.memory_type == MemoryType.INSIGHT.value))).scalar_one_or_none()
    if memory is None or (memory.meta or {}).get("kind") != "policy_suggestion":
        raise NotFoundError("suggestion not found")
    if (memory.meta or {}).get("status") != "open":
        raise ValidationFailedError("suggestion is no longer open", code="suggestion_closed")
    return memory


@router.post("/api/policies/suggestions/{memory_id}/accept", response_model=PolicyOut)
async def accept_suggestion(memory_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                            now: datetime = Depends(get_now)) -> PolicyOut:
    memory = await _suggestion(session, user, memory_id)
    rule_id = memory.meta["rule_id"]
    for policy in (await session.execute(select(Policy).where(Policy.user_id == user.id,
                                                              Policy.status == PolicyStatus.ACTIVE.value))).scalars():
        version = await current_version(session, policy)
        rules = [PolicyRule.model_validate(r) for r in version.rules]
        if any(r.rule_id == rule_id for r in rules):
            updated = [r.model_copy(update={"escalation": Escalation.ADAPTIVE}) if r.rule_id == rule_id else r for r in rules]
            policy, _ = await update_policy(session, user.id, policy.id, rules=updated,
                                            source_text=f"accepted suggestion: {rule_id} escalation=adaptive",
                                            compiled_by=CompiledBy.MANUAL, actor=f"user:{user.id}", now=now)
            memory.meta = {**memory.meta, "status": "accepted"}
            return await _policy_out(session, policy)
    raise NotFoundError("the rule for this suggestion no longer exists")


@router.post("/api/policies/suggestions/{memory_id}/dismiss", status_code=204)
async def dismiss_suggestion(memory_id: uuid.UUID, user: User = Depends(get_current_user),
                             session: AsyncSession = Depends(get_session)) -> None:
    memory = await _suggestion(session, user, memory_id)
    memory.meta = {**memory.meta, "status": "dismissed"}


@router.post("/api/overrides", response_model=OverrideOut, status_code=201)
async def add_override(body: OverrideCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                       now: datetime = Depends(get_now)) -> OverrideOut:
    prefs = await get_preferences(session, user.id)
    row = await create_override(session, user.id, body.kind, app_package=body.app_package, reason=body.reason,
                                tz=user_timezone(prefs), actor=f"user:{user.id}", now=now)
    return OverrideOut.model_validate(row)


@router.get("/api/overrides", response_model=list[OverrideOut])
async def get_overrides(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                        now: datetime = Depends(get_now)) -> list[OverrideOut]:
    active = {o.id for o in await active_overrides(session, user.id, now)}
    rows: list[PolicyOverride] = list((await session.execute(select(PolicyOverride).where(PolicyOverride.id.in_(active)))).scalars()) if active else []
    return [OverrideOut.model_validate(r) for r in rows]


@router.delete("/api/overrides/{override_id}", response_model=OverrideOut)
async def delete_override(override_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
                          now: datetime = Depends(get_now)) -> OverrideOut:
    return OverrideOut.model_validate(await revoke_override(session, user.id, override_id, actor=f"user:{user.id}", now=now))
