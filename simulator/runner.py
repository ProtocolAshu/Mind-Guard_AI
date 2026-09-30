"""Episode runner: simulate a persona under a controller and collect metrics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.bandit.rewards import compute_reward
from app.schemas.common import InterventionType as IT, OutcomeType
from simulator.controllers import Controller, Observation
from simulator.personas import Persona
from simulator.world import MAX_TICKS, TICK_MINUTES, continue_probability, plan_day, respond, satisfaction_delta

LONG_SESSION = 30
RESTRICTIONS = {IT.DELAY, IT.LIMITED_ACCESS, IT.TEMPORARY_BLOCK}


@dataclass
class DayMetrics:
    day: int
    social_minutes: float = 0.0
    sessions: int = 0
    long_sessions: int = 0
    unwanted_sessions: int = 0
    unwanted_minutes: float = 0.0
    focus_minutes: float = 0.0
    focus_distraction_minutes: float = 0.0
    interventions: int = 0
    accepted: int = 0
    overridden: int = 0
    ignored: int = 0
    false_positives: int = 0
    prevented_opens: int = 0
    satisfaction: float = 0.0
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    hourly: list[float] = field(default_factory=lambda: [0.0] * 24)

    def as_row(self) -> dict[str, Any]:
        n = self.interventions
        return {"day": self.day, "social_minutes": self.social_minutes, "sessions": self.sessions, "long_sessions": self.long_sessions,
                "unwanted_sessions": self.unwanted_sessions, "unwanted_minutes": self.unwanted_minutes,
                "goal_adherence": 1.0 - self.focus_distraction_minutes / self.focus_minutes if self.focus_minutes else 1.0,
                "interventions": n, "acceptance_rate": self.accepted / n if n else None,
                "override_rate": self.overridden / n if n else None, "false_positive_rate": self.false_positives / n if n else None,
                "prevented_opens": self.prevented_opens, "satisfaction": self.satisfaction / (1 + n) if n else 0.0,
                "accepted": self.accepted, "overridden": self.overridden, "ignored": self.ignored,
                "false_positives": self.false_positives, "satisfaction_sum": self.satisfaction,
                **{f"n_{k}": v for k, v in self.by_type.items()}}


def run_episode(persona: Persona, controller: Controller, *, seed: int, days: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in range(days):
        plan = plan_day(persona, seed, day)
        m = DayMetrics(day=day, focus_minutes=float(sum(e - s for s, e in plan.focus_blocks)))
        by_app: dict[str, float] = defaultdict(float)
        by_category: dict[str, float] = defaultdict(float)
        open_times: list[int] = []
        blocked_until: dict[str, int] = {}
        same_today: dict[IT, int] = defaultdict(int)
        for session in plan.sessions:
            start = session.start_minute
            order = (start - persona.wake_hour * 60) % 1440
            if blocked_until.get(session.app_package, -1) > order:
                if session.override_draws[0] >= persona.reactance * 0.5:
                    m.prevented_opens += 1
                    continue
            open_times.append(order)
            minutes, warned, boost, unwanted_counted = 0.0, False, 0.0, False
            for tick in range(MAX_TICKS):
                minute_of_day = (start + int(minutes)) % 1440
                in_focus = plan.in_focus(minute_of_day)
                if tick > 0:
                    p = continue_probability(persona, minutes=minutes, minute_of_day=minute_of_day, content=session.content,
                                             in_focus=in_focus, goal_relevant=session.goal_relevant, boost=boost)
                    if session.continue_draws[tick] >= p:
                        break
                minutes += TICK_MINUTES
                m.social_minutes += TICK_MINUTES
                m.hourly[minute_of_day // 60] += TICK_MINUTES
                by_app[session.app_package] += TICK_MINUTES
                by_category[session.app_category] += TICK_MINUTES
                if in_focus and not session.goal_relevant:
                    m.focus_distraction_minutes += TICK_MINUTES
                if not session.goal_relevant and (in_focus or minutes > LONG_SESSION):
                    m.unwanted_minutes += TICK_MINUTES
                    if not unwanted_counted:
                        m.unwanted_sessions += 1
                        unwanted_counted = True
                obs = Observation(day=day, weekday=plan.weekday, minute_of_day=minute_of_day, session=session,
                                  session_minutes=minutes, in_focus=in_focus, daily_social_minutes=m.social_minutes,
                                  daily_minutes_by_app=by_app, daily_minutes_by_category=by_category,
                                  opens_last_hour=sum(1 for t in open_times if order - 60 <= t <= order + minutes),
                                  warned_this_session=warned, plan=plan)
                decision = controller.decide(obs)
                if decision is None:
                    continue
                intervention, duration = decision
                outcome = respond(persona, intervention, draw=float(session.response_draws[tick]),
                                  override_draw=float(session.override_draws[tick]), same_today=same_today[intervention],
                                  goal_relevant=session.goal_relevant, in_focus=in_focus)
                same_today[intervention] += 1
                reward = compute_reward(outcome)
                m.interventions += 1
                m.by_type[intervention.value] += 1
                m.false_positives += session.goal_relevant
                m.satisfaction += satisfaction_delta(intervention, outcome, session.goal_relevant)
                controller.observe(obs, intervention, duration, outcome, reward)
                if outcome is OutcomeType.ACCEPTED:
                    m.accepted += 1
                    if intervention in RESTRICTIONS:
                        blocked_until[session.app_package] = order + int(minutes) + max(duration, 2)
                    break
                if outcome is OutcomeType.OVERRIDDEN:
                    m.overridden += 1
                    boost = min(0.03, boost + 0.01)  # reactance: overridden restrictions prolong the session a little
                else:
                    m.ignored += 1
                warned = True
            m.sessions += 1
            if minutes >= LONG_SESSION:
                m.long_sessions += 1
        controller.end_day({"day": day, "social_minutes": m.social_minutes, "sessions": m.sessions,
                            "long_sessions": m.long_sessions, "hourly": m.hourly})  # type: ignore[dict-item]
        rows.append(m.as_row())
    return rows
