"""Synthetic user personas: profile, habits, goals and intervention preferences."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.common import InterventionType as IT

SOCIAL_APPS = (
    ("com.instagram.android", "social_media"),
    ("com.google.android.youtube", "video"),
    ("com.zhiliaoapp.musically", "video"),
    ("com.snapchat.android", "social_media"),
    ("com.twitter.android", "social_media"),
)


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    opens_per_waking_hour: float          # baseline impulsivity
    peak_hours: tuple[int, ...]           # habitual trigger hours (local)
    peak_multiplier: float                # open-rate multiplier during peak hours
    night_owl: float                      # 0..1 propensity to use apps after 22:00
    binge: float                          # 0..1 session continuation tendency
    commitment: float                     # 0..1 willingness to follow nudges toward the goal
    reactance: float                      # 0..1 autonomy sensitivity: overrides hard restrictions
    habituation: float                    # compliance decay per repeated same intervention in a day
    goal_relevant_share: float            # share of sessions with genuinely goal-relevant content
    study_blocks: tuple[tuple[int, int], ...]  # (start_hour, minutes) focus sessions on weekdays
    affinity: dict[IT, float] = field(default_factory=dict)  # per-intervention compliance multipliers
    constitution: str = ""
    style: str = "balanced"
    wake_hour: int = 7
    sleep_hour: int = 1


PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="night_owl_student",
        description="Placement-prep student; long late-night scrolling; responds to delays, resents blocks.",
        opens_per_waking_hour=0.45, peak_hours=(22, 23, 0), peak_multiplier=2.4, night_owl=0.85, binge=0.8,
        commitment=0.6, reactance=0.75, habituation=0.12, goal_relevant_share=0.18,
        study_blocks=((10, 90), (15, 60), (20, 60)),
        affinity={IT.DELAY: 1.35, IT.MINDFUL_PROMPT: 1.1, IT.TEMPORARY_BLOCK: 0.6, IT.LIMITED_ACCESS: 0.8},
        constitution=("I am preparing for placements. Don't allow short videos during study sessions. "
                      "After 11 PM, block entertainment. If I exceed 45 minutes of social media, ask me before continuing."),
        style="balanced", sleep_hour=2,
    ),
    Persona(
        name="focused_but_impulsive",
        description="Disciplined planner with frequent impulsive checks during focus blocks; complies with firm rules.",
        opens_per_waking_hour=0.7, peak_hours=(11, 16), peak_multiplier=1.6, night_owl=0.25, binge=0.45,
        commitment=0.8, reactance=0.25, habituation=0.08, goal_relevant_share=0.3,
        study_blocks=((9, 120), (14, 120)),
        affinity={IT.TEMPORARY_BLOCK: 1.2, IT.LIMITED_ACCESS: 1.15, IT.SOFT_WARNING: 0.8},
        constitution="I am preparing for my GATE exam. Don't allow social media during study sessions.",
        style="strict",
    ),
    Persona(
        name="reactant_scroller",
        description="Strongly autonomy-sensitive; overrides most hard restrictions; gentle prompts work best.",
        opens_per_waking_hour=0.5, peak_hours=(13, 21, 22), peak_multiplier=2.0, night_owl=0.6, binge=0.7,
        commitment=0.45, reactance=0.9, habituation=0.15, goal_relevant_share=0.12,
        study_blocks=((18, 60),),
        affinity={IT.MINDFUL_PROMPT: 1.5, IT.REQUEST_CONFIRMATION: 1.3, IT.TEMPORARY_BLOCK: 0.35, IT.LIMITED_ACCESS: 0.5},
        constitution="I want to finish my thesis. Warn me gently if I start scrolling late at night. Limit entertainment.",
        style="gentle",
    ),
    Persona(
        name="compliant_planner",
        description="Moderate user who follows most interventions; low baseline risk.",
        opens_per_waking_hour=0.3, peak_hours=(20,), peak_multiplier=1.5, night_owl=0.2, binge=0.35,
        commitment=0.85, reactance=0.2, habituation=0.05, goal_relevant_share=0.35,
        study_blocks=((10, 60), (16, 60)),
        affinity={IT.SOFT_WARNING: 1.2},
        constitution="I am learning machine learning. Only allow educational content during study sessions.",
        style="balanced",
    ),
    Persona(
        name="binge_watcher",
        description="Evening video binges; content mostly entertainment; limited access works, reminders do not.",
        opens_per_waking_hour=0.35, peak_hours=(19, 20, 21), peak_multiplier=2.2, night_owl=0.5, binge=0.9,
        commitment=0.55, reactance=0.5, habituation=0.1, goal_relevant_share=0.1,
        study_blocks=((17, 60),),
        affinity={IT.LIMITED_ACCESS: 1.4, IT.DELAY: 1.1, IT.SOFT_WARNING: 0.6, IT.MINDFUL_PROMPT: 0.8},
        constitution="Limit YouTube to 60 minutes a day. After 11 PM, block entertainment.",
        style="balanced",
    ),
)
PERSONA_BY_NAME = {p.name: p for p in PERSONAS}
