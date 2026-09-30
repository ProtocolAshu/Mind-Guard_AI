"""Goal Agent (section 4.2): natural-language constitution -> validated policy preview.

Deterministic compiler first; the LLM sees only sentences the compiler could not parse,
grounded with retrieved platform-capability documentation. Every LLM-proposed rule
must pass the same schema and `validate_rules` checks; failures become unsupported
requests with a reason instead of silently disappearing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.agents.prompts import system_prompt
from app.agents.runtime import RunContext
from app.policies.catalog import APPS
from app.policies.compiler import summarize_constitution
from app.providers.base import ChatMessage, LLMRequest
from app.rag.knowledge import search_knowledge
from app.schemas.common import CompiledBy, ContentCategory
from app.schemas.llm import ConstitutionCompileOutput
from app.schemas.policy import CompiledConstitution, PolicyRule, UnsupportedRequest, ValidationReport
from app.security.injection import wrap_untrusted


@dataclass
class ConstitutionPreview:
    constitution: CompiledConstitution
    compiled_by: CompiledBy
    validation: ValidationReport
    parsed: list[str]
    unparsed: list[str]
    summary: dict[str, Any]
    llm_used: bool = False
    grounding: list[str] = field(default_factory=list)


async def compile_constitution(ctx: RunContext, text: str, *, allow_llm: bool = True) -> ConstitutionPreview:
    det = ctx.deps.compiler.compile(text)
    rules: list[PolicyRule] = list(det.constitution.rules)
    unsupported: list[UnsupportedRequest] = list(det.constitution.unsupported)
    clarifications: list[str] = list(det.constitution.clarifications)
    goal = det.constitution.goal
    compiled_by, llm_used, grounding = CompiledBy.DETERMINISTIC, False, []
    if det.unparsed and allow_llm and ctx.cloud_ai_allowed:
        hits = await search_knowledge(ctx.session, ctx.deps.embedder, " ".join(det.unparsed), k=2)
        grounding = [f"{h.title}#{h.chunk_index}" for h in hits]
        payload = {
            "task": "compile_constitution",
            "existing_rule_ids": [r.rule_id for r in rules],
            "app_catalog": [{"package": a.package, "name": a.name, "category": a.category.value} for a in APPS],
            "content_categories": [c.value for c in ContentCategory],
            "capability_notes": [h.content[:700] for h in hits],
        }
        message = json.dumps(payload) + "\nUnparsed sentences written by the account owner:\n" + wrap_untrusted(
            "\n".join(det.unparsed[:10]), "user_text")
        request = LLMRequest(purpose="constitution_compile", system=system_prompt("goal"), tier="reasoning", max_tokens=1200,
                             messages=(ChatMessage("user", message),))
        result = await ctx.gateway.generate(request, ConstitutionCompileOutput, user_id=ctx.user_id, run_id=ctx.run_id)
        if result.ok and result.parsed is not None:
            llm_used = True
            used = {r.rule_id for r in rules}
            for candidate in result.parsed.rules:
                rule = candidate
                n = 2
                while rule.rule_id in used:
                    rule = candidate.model_copy(update={"rule_id": f"{candidate.rule_id[:36]}_{n}"})
                    n += 1
                report = ctx.deps.policy_engine.validate([*rules, rule])
                if any(i.severity == "error" and i.rule_id == rule.rule_id for i in report.issues):
                    unsupported.append(UnsupportedRequest(text=rule.source_text or rule.description,
                                                          reason="; ".join(i.message for i in report.issues
                                                                           if i.rule_id == rule.rule_id)[:300]))
                    continue
                rules.append(rule)
                used.add(rule.rule_id)
                compiled_by = CompiledBy.LLM
            unsupported.extend(result.parsed.unsupported)
            clarifications.extend(result.parsed.clarifications)
            goal = goal or result.parsed.goal
        else:
            clarifications.append("AI compilation is unavailable right now; please rephrase the sentences that were not understood.")
    elif det.unparsed and allow_llm:
        clarifications.append("Some sentences were not understood. Rephrase them with an app or content type, a time and an action.")
    constitution = CompiledConstitution(goal=goal, rules=rules[:50], unsupported=unsupported[:20],
                                        clarifications=list(dict.fromkeys(clarifications))[:10])
    return ConstitutionPreview(constitution=constitution, compiled_by=compiled_by, validation=ctx.deps.policy_engine.validate(constitution.rules),
                               parsed=det.parsed, unparsed=det.unparsed, summary=summarize_constitution(constitution),
                               llm_used=llm_used, grounding=grounding)
