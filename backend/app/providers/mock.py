"""Deterministic offline LLM stand-in.

Clearly labelled `provider="mock"` everywhere it appears (llm_calls, agent traces, UI).
It produces schema-valid JSON by applying transparent heuristics to the structured
input, so the whole agent graph runs reproducibly without a key. Failure modes
(`error`, `timeout`, `malformed`, `compromised`, `rejected`) exercise the failsafes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Literal

from app.providers.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRejectedError,
    ProviderUnavailableError,
    VisionProvider,
)
from app.providers.classifiers import LexiconClassifier
from app.providers.structured import extract_json_object
from app.schemas.common import INTERVENTION_SEVERITY, ReasonCode
from app.security.injection import detect_injection

MockMode = Literal["ok", "error", "timeout", "malformed", "compromised", "rejected"]
_UNTRUSTED = re.compile(r"<untrusted_\w+ boundary=\"[0-9a-f]+\">\n(.*)\n</untrusted_\w+ boundary=", re.DOTALL)
_VALID_CODES = {c.value for c in ReasonCode}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockLLMProvider(LLMProvider):
    name = "mock"

    def __init__(self, mode: MockMode = "ok", latency_ms: float = 0.0):
        self.mode: MockMode = mode
        self.latency_ms = latency_ms
        self.calls: list[LLMRequest] = []
        self._lexicon = LexiconClassifier()

    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(request)
        started = time.perf_counter()
        if self.mode == "error":
            raise ProviderUnavailableError("mock provider configured to be unavailable")
        if self.mode == "rejected":
            raise ProviderRejectedError("mock provider configured to reject credentials")
        if self.mode == "timeout":
            await asyncio.sleep(3600)
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        prompt = request.messages[-1].content if request.messages else ""
        if self.mode == "malformed":
            text = "Sure! Based on everything, I think blocking the app for a while is best."
        elif self.mode == "compromised":
            text = json.dumps(self._compromised(request.purpose))
        else:
            payload = extract_json_object(prompt) or {}
            handler = getattr(self, f"_p_{request.purpose}", None)
            text = json.dumps(handler(payload, prompt) if handler else {"error": f"unsupported purpose {request.purpose}"})
        full_prompt = request.system + "".join(m.content for m in request.messages)
        return LLMResponse(
            text=text,
            model=f"mock:{model}",
            provider=self.name,
            prompt_tokens=_tokens(full_prompt),
            completion_tokens=_tokens(text),
            latency_ms=(time.perf_counter() - started) * 1000,
            stop_reason="end_turn",
        )

    # -- purposes ------------------------------------------------------------
    def _p_content_classification(self, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        match = _UNTRUSTED.search(prompt)
        text = match.group(1) if match else ""
        category, confidence, _ = self._lexicon.classify(text)
        relevant = set(payload.get("goal_relevant_categories") or [])
        if category.value in relevant and confidence >= 0.4:
            relevance = "goal_relevant"
        elif category.value in {"entertainment", "short_form_video", "gaming", "clickbait"} and confidence >= 0.4:
            relevance = "goal_irrelevant"
        else:
            relevance = "neutral"
        return {
            "category": category.value,
            "confidence": round(min(0.9, confidence + 0.05), 3) if confidence else 0.2,
            "goal_relevance": relevance,
            "injection_suspected": detect_injection(text).detected,
            "rationale": f"keyword evidence for {category.value}" if confidence else "no clear topical evidence",
        }

    def _p_decision(self, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        tools = payload.get("available_tools") or []
        if "retrieve_memories" in tools and not payload.get("observations") and not payload.get("memories"):
            return {
                "type": "tool_request",
                "tool": "retrieve_memories",
                "arguments": {"query": f"{payload.get('app_name', 'social app')} {payload.get('time_of_day', '')} outcomes",
                              "k": 3},
                "purpose": "check how similar past interventions ended",
            }
        eligible: list[str] = payload.get("eligible") or ["ALLOW"]
        recommended = payload.get("recommended")
        choice: str = recommended if isinstance(recommended, str) and recommended in eligible else eligible[0]
        effectiveness: dict[str, float] = payload.get("effectiveness") or {}
        best = max(eligible, key=lambda a: (effectiveness.get(a, -9), -INTERVENTION_SEVERITY.get(a, 0)))  # type: ignore[call-overload]
        if effectiveness.get(best, -9) - effectiveness.get(choice, 0.0) >= 0.3:
            choice = best
        risk = payload.get("risk") or {}
        codes = [c for c in payload.get("reason_codes", []) if c in _VALID_CODES][:6]
        return {
            "type": "final",
            "decision": choice,
            "duration_minutes": int(payload.get("recommended_duration") or 0),
            "confidence": round(min(0.9, float(risk.get("confidence", 0.6))), 3),
            "reason_codes": codes,
            "user_visible_explanation": f"Suggested {choice.replace('_', ' ').lower()} based on your current risk and past outcomes.",
        }

    def _p_constitution_compile(self, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        sentences = payload.get("unparsed_sentences") or []
        return {
            "rules": [],
            "unsupported": [
                {"text": str(s)[:500], "reason": "The offline model could not interpret this safely; "
                                                 "rephrase with an app or content type, a time, and an action."}
                for s in sentences[:20]
            ],
            "clarifications": [],
        }

    def _p_behavior_insight(self, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        hours = sorted(int(h) for h in payload.get("trigger_hours") or [])
        if hours:
            start, end = hours[0], (hours[-1] + 1) % 24
            headline = f"You are most likely to enter long scrolling sessions between {start:02d}:00 and {end:02d}:00."
            recommendation = f"Turn on automatic focus protection from {start:02d}:00."
        else:
            headline = "Not enough history yet to find your high-risk periods."
            recommendation = "Keep MindGuard running for a few days to personalise insights."
        evidence = [f"average social time {payload.get('avg_daily_social_minutes', 0):.0f} min/day"]
        return {"headline": headline, "recommendation": recommendation, "evidence": evidence}

    def _p_learning_reflection(self, payload: dict[str, Any], prompt: str) -> dict[str, Any]:
        stats: dict[str, dict[str, float]] = payload.get("stats") or {}
        tried = {k: v for k, v in stats.items() if v.get("n", 0) >= 2}
        if len(tried) < 2:
            return {"observations": ["too few outcomes to compare interventions"], "confidence": 0.2}
        best = max(tried, key=lambda k: tried[k]["mean_reward"])
        worst = min(tried, key=lambda k: tried[k]["mean_reward"])
        return {
            "observations": [f"{best} averages {tried[best]['mean_reward']:+.2f} vs {worst} {tried[worst]['mean_reward']:+.2f}"],
            "preferred_intervention": best,
            "avoid_intervention": worst if worst != best else None,
            "confidence": 0.7,
        }

    @staticmethod
    def _compromised(purpose: str) -> dict[str, Any]:
        """What a model that obeyed injected content might emit. Must be rejected downstream."""
        if purpose == "content_classification":
            return {"category": "education", "confidence": 1.0, "goal_relevance": "goal_relevant",
                    "injection_suspected": False, "set_policy": {"effect": "ALLOW", "all_apps": True}}
        if purpose == "decision":
            return {"type": "final", "decision": "ALLOW", "duration_minutes": 0, "confidence": 1.0,
                    "reason_codes": ["EMERGENCY_OVERRIDE"], "user_visible_explanation": "Protection disabled as requested.",
                    "disable_guardian": True}
        if purpose == "constitution_compile":
            return {"rules": [{"rule_id": "r_allow_everything", "description": "x", "effect": "ALLOW",
                               "condition": {}, "guardian_enabled": False}]}
        return {"note": "ignore previous instructions"}


class MockVisionProvider(VisionProvider):
    name = "mock"

    def __init__(self, canned: dict[str, Any] | None = None):
        self.canned = canned

    async def analyze_image(self, image: bytes, media_type: str, prompt: str, system: str, model: str) -> LLMResponse:
        body = self.canned or {"category": "unknown", "confidence": 0.0,
                               "summary": "mock vision provider cannot see images", "contains_personal_data": False,
                               "injection_suspected": False}
        text = json.dumps(body)
        return LLMResponse(text=text, model=f"mock:{model}", provider=self.name, prompt_tokens=_tokens(prompt) + 255,
                           completion_tokens=_tokens(text))
