"""Action Agent (section 4.9) and explanation composer (section 13).

Validated decisions become device commands. Every command is authorized (ticket),
logged (intervention row + audit entry), explainable (reasons) and reversible
(Allow once / Emergency actions are always attached to restrictions)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.interfaces import InterventionController
from app.schemas.agents import (
    AuthorizationResult,
    CommandAction,
    CommandName,
    ContextSnapshot,
    DeviceCommand,
    RiskAssessment,
)
from app.schemas.common import InterventionType, ReasonCode
from app.schemas.policy import PolicyRule

COMMANDS: dict[InterventionType, tuple[CommandName, list[CommandAction]]] = {
    InterventionType.ALLOW: ("NONE", []),
    InterventionType.SOFT_WARNING: ("NOTIFY", ["ACCEPT", "CONTINUE"]),
    InterventionType.MINDFUL_PROMPT: ("MINDFUL_PROMPT", ["STOP", "CONTINUE"]),
    InterventionType.REQUEST_CONFIRMATION: ("CONFIRM", ["STOP", "CONTINUE"]),
    InterventionType.DELAY: ("DELAY_GATE", ["ACCEPT", "ALLOW_ONCE", "EMERGENCY"]),
    InterventionType.LIMITED_ACCESS: ("LIMIT_ACCESS", ["ACCEPT", "ALLOW_ONCE", "EMERGENCY"]),
    InterventionType.TEMPORARY_BLOCK: ("BLOCK_APP", ["ACCEPT", "ALLOW_ONCE", "EMERGENCY"]),
    InterventionType.FOCUS_MODE: ("START_FOCUS", ["START_FOCUS", "CONTINUE"]),
}
SOFT_TTL_MINUTES = 10


def expiry_for(decision: InterventionType, duration_minutes: int, now: datetime) -> datetime | None:
    if decision is InterventionType.ALLOW:
        return None
    return now + timedelta(minutes=duration_minutes if duration_minutes > 0 else SOFT_TTL_MINUTES)


def build_command(decision: InterventionType, duration_minutes: int, *, intervention_id: uuid.UUID | None,
                  app_package: str | None, title: str, body: str, now: datetime) -> DeviceCommand:
    name, actions = COMMANDS[decision]
    return DeviceCommand(command=name, intervention_id=intervention_id, app_package=app_package,
                         duration_seconds=max(0, duration_minutes) * 60, title=title[:80], body=body[:400],
                         actions=actions, expires_at=expiry_for(decision, duration_minutes, now),
                         reversible=decision is not InterventionType.ALLOW)


class AndroidCommandController(InterventionController):
    """Default InterventionController: commands for the MindGuard Android app (android/intervention executes them)."""

    name = "android-device-command"

    def build(self, decision: InterventionType, duration_minutes: int, *, intervention_id: uuid.UUID | None,
              app_package: str | None, title: str, body: str, now: datetime) -> DeviceCommand:
        return build_command(decision, duration_minutes, intervention_id=intervention_id, app_package=app_package,
                             title=title, body=body, now=now)

    def expiry(self, decision: InterventionType, duration_minutes: int, now: datetime) -> datetime | None:
        return expiry_for(decision, duration_minutes, now)


def _headline(decision: InterventionType, duration: int, app: str) -> str:
    return {
        InterventionType.ALLOW: f"{app} is allowed.",
        InterventionType.SOFT_WARNING: f"Heads-up about {app}.",
        InterventionType.MINDFUL_PROMPT: f"Quick check-in before more {app}.",
        InterventionType.REQUEST_CONFIRMATION: f"Is this {app} session intentional?",
        InterventionType.DELAY: f"{app} will open after a {duration}-minute pause because:",
        InterventionType.LIMITED_ACCESS: f"{app} access was limited for {duration} minutes because:",
        InterventionType.TEMPORARY_BLOCK: f"{app} is paused for {duration} minutes because:",
        InterventionType.FOCUS_MODE: f"A {duration}-minute focus session is suggested because:",
    }[decision]


def compose_explanation(*, decision: InterventionType, duration: int, context: ContextSnapshot | None,
                        risk: RiskAssessment | None, rule: PolicyRule | None, reason_codes: list[ReasonCode],
                        authorization: AuthorizationResult | None, llm_sentence: str = "") -> tuple[str, list[str]]:
    app = (context.app_name if context else None) or "This app"
    points: list[str] = []

    def add(text: str) -> None:
        if text and text not in points:
            points.append(text)

    flags = set(authorization.flags) if authorization else set()
    terminal = {ReasonCode.EMERGENCY_OVERRIDE: "Emergency override is active: every restriction is released.",
                ReasonCode.GUARDIAN_PAUSED: "You paused the guardian.",
                ReasonCode.GUARDIAN_DISABLED: "The guardian is turned off.",
                ReasonCode.ESSENTIAL_APP_PROTECTED: "Essential apps are never restricted.",
                ReasonCode.CONSENT_MISSING: "Usage monitoring is not enabled, so MindGuard does not act.",
                ReasonCode.ALLOW_ONCE: "You chose 'Allow once' for this app."}
    for code, text in terminal.items():
        if code in flags:
            add(text)
    codes = [*reason_codes, *flags]
    session_minutes = int(context.session_minutes) if context else 0
    for code in codes:
        match code:
            case ReasonCode.FOCUS_SESSION_ACTIVE:
                add("You are in a focus session.")
            case ReasonCode.LONG_SESSION if session_minutes >= 5:
                add(f"Your current session has lasted {session_minutes} minutes.")
            case ReasonCode.HIGH_RISK_SESSION | ReasonCode.ELEVATED_RISK_SESSION if risk is not None:
                level = "high" if code is ReasonCode.HIGH_RISK_SESSION else "elevated"
                add(f"Your distraction risk is {level} right now ({risk.distraction_risk:.0%}).")
            case ReasonCode.TRIGGER_HOUR:
                add("This is one of your usual high-usage hours.")
            case ReasonCode.NIGHT_USAGE:
                add("It is late at night.")
            case ReasonCode.POLICY_RULE_MATCHED if rule is not None:
                add(f"Your policy applies: {rule.description}.")
            case ReasonCode.POLICY_EXCEPTION_ALLOWS if rule is not None:
                add(f"Your exception applies: {rule.description}.")
            case ReasonCode.ENTERTAINMENT_CONTENT:
                add("The content looks like entertainment.")
            case ReasonCode.GOAL_RELEVANT_CONTENT:
                add("The content looks relevant to your goal.")
            case ReasonCode.DAILY_LIMIT_EXCEEDED if context is not None:
                add(f"You have spent {int(context.daily_social_minutes)} minutes on social apps today.")
            case ReasonCode.REPEATED_OPENS if context is not None:
                add(f"You opened social apps {context.opens_last_hour} times in the last hour.")
            case ReasonCode.MEMORY_PATTERN:
                add("A gentler option has worked better for you before.")
            case ReasonCode.PERSONALIZED_CHOICE:
                add("Chosen based on what has worked for you in similar moments.")
            case ReasonCode.LOW_CONFIDENCE:
                add("MindGuard is not certain, so this is only a heads-up.")
            case ReasonCode.CONTENT_UNCERTAIN:
                add("MindGuard could not tell whether this content falls under your rule.")
            case ReasonCode.UNTRUSTED_CONTENT_QUARANTINED:
                add("The content contained instructions aimed at AI systems; they were ignored.")
            case _:
                pass
    if authorization is not None:
        for note in authorization.notes:
            if not any(note == t for t in terminal.values()):
                add(note)
    if llm_sentence:
        add(llm_sentence)
    if decision is not InterventionType.ALLOW and not any(p for p in points):
        add("Your current usage pattern matches your protection settings.")
    return _headline(decision, duration, app), points[:5]
