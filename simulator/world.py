"""Synthetic world: day plans (focus blocks, app opens, content) and the user response model.

Common random numbers: every random quantity is drawn from a stream keyed by
(seed, day, session, purpose), so all controllers face identical days and identical
"coin flips" — differences in outcomes come from the controllers, not from noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.schemas.common import INTERVENTION_SEVERITY, ContentCategory, InterventionType as IT, OutcomeType
from simulator.personas import SOCIAL_APPS, Persona

TICK_MINUTES = 5
MAX_TICKS = 48  # 240 minutes cap per session
ENTERTAINMENT = (ContentCategory.SHORT_FORM_VIDEO, ContentCategory.ENTERTAINMENT, ContentCategory.GAMING, ContentCategory.CLICKBAIT)
ALL_OBSERVABLE = (ContentCategory.EDUCATION, ContentCategory.CAREER, ContentCategory.ENTERTAINMENT,
                  ContentCategory.SHORT_FORM_VIDEO, ContentCategory.SOCIAL, ContentCategory.NEWS, ContentCategory.GAMING)
BASE_ACCEPT = {IT.SOFT_WARNING: 0.22, IT.MINDFUL_PROMPT: 0.32, IT.REQUEST_CONFIRMATION: 0.38, IT.DELAY: 0.5,
               IT.FOCUS_MODE: 0.4, IT.LIMITED_ACCESS: 0.62, IT.TEMPORARY_BLOCK: 0.78}
HARD = {IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK}


def stream(seed: int, *keys: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed & 0xFFFFFFFF, *[k & 0xFFFFFFFF for k in keys]]))


@dataclass
class PlannedSession:
    index: int
    start_minute: int              # minutes since local midnight of the simulated day
    app_package: str
    app_category: str
    content: ContentCategory
    goal_relevant: bool
    continue_draws: np.ndarray     # uniform draws, one per tick
    response_draws: np.ndarray     # uniform draws, one per tick (for intervention responses)
    override_draws: np.ndarray
    observed_content: ContentCategory = ContentCategory.UNKNOWN   # what a classifier would report
    observed_confidence: float = 0.0


@dataclass
class DayPlan:
    day: int
    weekday: int
    focus_blocks: list[tuple[int, int]]      # (start_minute, end_minute)
    sessions: list[PlannedSession] = field(default_factory=list)

    def in_focus(self, minute: int) -> bool:
        return any(s <= minute < e for s, e in self.focus_blocks)


def _waking(persona: Persona, hour: int) -> bool:
    if persona.sleep_hour < persona.wake_hour:
        return not (persona.sleep_hour <= hour < persona.wake_hour)
    return persona.wake_hour <= hour < persona.sleep_hour


def plan_day(persona: Persona, seed: int, day: int) -> DayPlan:
    rng = stream(seed, day, 1)
    weekday = day % 7
    blocks: list[tuple[int, int]] = []
    if weekday < 5:
        for start_hour, minutes in persona.study_blocks:
            if rng.random() < 0.85:
                start = start_hour * 60 + int(rng.integers(0, 20))
                blocks.append((start, start + minutes))
    plan = DayPlan(day=day, weekday=weekday, focus_blocks=blocks)
    index = 0
    for hour in list(range(persona.wake_hour, 24)) + list(range(0, persona.wake_hour)):
        if not _waking(persona, hour):
            continue
        rate = persona.opens_per_waking_hour
        if hour in persona.peak_hours:
            rate *= persona.peak_multiplier
        if hour >= 22 or hour < 5:
            rate *= 0.4 + persona.night_owl
        for _ in range(int(rng.poisson(rate))):
            minute = (hour * 60 + int(rng.integers(0, 60))) % 1440
            app, category = SOCIAL_APPS[int(rng.choice(len(SOCIAL_APPS), p=[0.32, 0.3, 0.18, 0.1, 0.1]))]
            relevant = bool(rng.random() < persona.goal_relevant_share)
            if relevant:
                content = ContentCategory.EDUCATION if rng.random() < 0.7 else ContentCategory.CAREER
            elif app == "com.zhiliaoapp.musically":
                content = ContentCategory.SHORT_FORM_VIDEO
            else:
                content = ENTERTAINMENT[int(rng.choice(4, p=[0.35, 0.4, 0.1, 0.15]))] if rng.random() < 0.8 else ContentCategory.SOCIAL
            srng = stream(seed, day, 100 + index)
            continue_draws, response_draws, override_draws = srng.random(MAX_TICKS), srng.random(MAX_TICKS), srng.random(MAX_TICKS)
            if srng.random() < 0.85:  # classifier noise: 85% correct
                observed, confidence = content, float(srng.uniform(0.6, 0.92))
            else:
                observed, confidence = ALL_OBSERVABLE[int(srng.integers(0, len(ALL_OBSERVABLE)))], float(srng.uniform(0.35, 0.7))
            plan.sessions.append(PlannedSession(index=index, start_minute=minute, app_package=app, app_category=category,
                                                content=content, goal_relevant=relevant, continue_draws=continue_draws,
                                                response_draws=response_draws, override_draws=override_draws,
                                                observed_content=observed, observed_confidence=confidence))
            index += 1
    plan.sessions.sort(key=lambda s: (s.start_minute - persona.wake_hour * 60) % 1440)
    return plan


def continue_probability(persona: Persona, *, minutes: float, minute_of_day: int, content: ContentCategory, in_focus: bool,
                         goal_relevant: bool, boost: float = 0.0) -> float:
    hour = minute_of_day // 60
    night = hour >= 22 or hour < 5
    p = 0.35 + 0.4 * persona.binge - 0.04 * math.log1p(minutes / 10)
    p += 0.08 * night * persona.night_owl + (0.07 if content in ENTERTAINMENT else 0.0)
    p -= 0.12 * in_focus * persona.commitment * (0 if goal_relevant else 1)
    p += 0.1 if goal_relevant else 0.0
    return float(min(0.96, max(0.05, p + boost)))


def respond(persona: Persona, intervention: IT, *, draw: float, override_draw: float, same_today: int, goal_relevant: bool,
            in_focus: bool) -> OutcomeType:
    """Stochastic, persona-specific response.

    Soft interventions (warning, prompt, confirmation, focus suggestion) are accepted or ignored.
    Restrictions (delay, limited access, block) are complied with or overridden; override
    probability grows with the persona's reactance, the severity, habituation, and — strongly —
    when the restriction interrupts goal-relevant content.
    """
    habituation = min(1.6, 1.0 + persona.habituation * same_today)
    affinity = persona.affinity.get(intervention, 1.0)
    if intervention in HARD:
        severity = INTERVENTION_SEVERITY[intervention]
        p_override = persona.reactance * (0.3 + 0.1 * severity) * habituation / affinity
        p_override += 0.4 if goal_relevant else 0.0
        p_override -= 0.2 * persona.commitment if (in_focus and not goal_relevant) else 0.0
        return OutcomeType.OVERRIDDEN if override_draw < min(0.95, max(0.02, p_override)) else OutcomeType.ACCEPTED
    accept = BASE_ACCEPT[intervention] * affinity * (0.55 + 0.45 * persona.commitment) / habituation
    if in_focus and not goal_relevant:
        accept *= 1.2
    if goal_relevant:
        accept *= 0.35
    return OutcomeType.ACCEPTED if draw < min(0.95, accept) else OutcomeType.IGNORED


def satisfaction_delta(intervention: IT, outcome: OutcomeType, goal_relevant: bool) -> float:
    delta = {OutcomeType.ACCEPTED: 0.25, OutcomeType.IGNORED: -0.1, OutcomeType.OVERRIDDEN: -0.6}.get(outcome, 0.0)
    delta -= 0.05 * INTERVENTION_SEVERITY[intervention]
    if goal_relevant:
        delta -= 0.5  # false positive: interrupted goal-relevant content
    return delta
