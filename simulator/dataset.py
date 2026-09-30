"""Privacy-safe synthetic dataset generator (section 28).

Outputs (under the chosen directory):
  events.jsonl          client events in the exact POST /api/events schema
  sessions.csv          one row per simulated session with ground-truth labels
  risk_training.csv     RISK_FEATURES at every 5-minute check + outcome labels (grouped by persona/seed)
  interventions.csv     logged interventions under a randomized safe logging policy, with propensities
  content_captions.csv  labelled synthetic captions for the content classifier
  goals.json            persona goals and constitutions
No real person's data is used anywhere.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.bandit.rewards import compute_reward
from app.policies.compiler import ConstitutionCompiler
from app.policies.engine import evaluate_policy
from app.risk.features import RISK_FEATURES, RiskInput, featurize
from app.schemas.common import AppCategory, ContentCategory, InterventionType as IT
from app.schemas.policy import PolicyContext
from simulator.controllers import BASE_DATE
from simulator.personas import PERSONAS, Persona
from simulator.world import MAX_TICKS, TICK_MINUTES, continue_probability, plan_day, respond, stream

LOGGING_ACTIONS = (IT.SOFT_WARNING, IT.MINDFUL_PROMPT, IT.REQUEST_CONFIRMATION, IT.DELAY, IT.LIMITED_ACCESS)
LOGGING_RATE = 0.12

CAPTION_TEMPLATES: dict[ContentCategory, list[str]] = {
    ContentCategory.EDUCATION: ["Lecture {n}: {topic} explained", "{topic} full course for beginners", "NPTEL week {n}: {topic}",
                                "How to solve {topic} problems step by step", "{topic} in one shot | revision notes",
                                "MIT OpenCourseWare {topic} lecture {n}", "Class {n} {topic} derivation and examples"],
    ContentCategory.CAREER: ["{company} interview experience for {role}", "Resume tips for {role} freshers",
                             "Placement preparation roadmap {year}", "Mock interview: {role} system design round",
                             "How I cracked {company} internship", "Salary negotiation tips for new grads"],
    ContentCategory.TECHNOLOGY: ["{lang} tutorial: {concept}", "Building a {project} with {lang}", "{gadget} review after 30 days",
                                 "What's new in {lang} {n}.0", "Deploying {project} on Kubernetes", "{concept} in {lang} explained with code"],
    ContentCategory.ENTERTAINMENT: ["Funniest {thing} moments of {year}", "{celebrity} reacts to {thing}", "Try not to laugh challenge #{n}",
                                    "{celebrity} new movie trailer reaction", "Prank on my roommate gone hilarious", "Best comedy sketches compilation"],
    ContentCategory.SHORT_FORM_VIDEO: ["#shorts {thing} in 30 seconds", "wait for it #reels #fyp", "POV: {situation} #shorts",
                                       "this trend is everywhere #reels", "{thing} hack you need #shorts #viral"],
    ContentCategory.NEWS: ["Breaking: {event} live updates", "{city} {event}: what we know so far", "Top headlines today: {event}",
                           "Government announces {policy} reform", "Explained: why {event} matters"],
    ContentCategory.SPORTS: ["{team} vs {team2} highlights | IPL {year}", "{player} century full innings", "Match recap: {team} win thriller",
                             "Top 10 goals of the {league} season", "Post-match press conference {team}"],
    ContentCategory.GAMING: ["{game} ranked gameplay {n} kills", "{game} funny moments montage", "Let's play {game} part {n}",
                             "{game} speedrun world record attempt", "Best {game} settings for {n} fps"],
    ContentCategory.CLICKBAIT: ["You won't believe what {celebrity} did next!!!", "SHOCKING truth about {thing} EXPOSED",
                                "Doctors hate this {thing} secret", "What happens next will blow your mind", "{n} secrets they don't want you to know"],
    ContentCategory.ADVERTISING: ["{brand} sale: {n}% off today only, shop now", "Sponsored: try {brand} free for {n} days",
                                  "Limited offer on {gadget}, order now", "Use promo code SAVE{n} at {brand}"],
    ContentCategory.SOCIAL: ["Weekend vibes with the gang", "Birthday dump with friends", "Throwback to {city} trip",
                             "Wedding season outfits ootd", "Story time: my {situation}"],
    ContentCategory.PRODUCTIVITY: ["My {n}-hour deep work routine", "Pomodoro study timer with lofi", "Notion template for {role}s",
                                   "How I plan my week for maximum focus", "Habits that fixed my time management"],
}
FILL = {
    "n": [str(i) for i in range(1, 40)], "topic": ["dynamic programming", "thermodynamics", "linear algebra", "organic chemistry",
    "graph theory", "fluid mechanics", "probability", "operating systems", "microeconomics", "control systems"],
    "company": ["Google", "Amazon", "Flipkart", "Microsoft", "Zomato", "Infosys"], "role": ["SDE", "data analyst", "ML engineer", "PM"],
    "year": ["2024", "2025", "2026"], "lang": ["Python", "Rust", "Java", "TypeScript", "Go"],
    "concept": ["async await", "closures", "generics", "memory management", "decorators"],
    "project": ["chat app", "URL shortener", "recommendation engine", "portfolio site"], "gadget": ["iPhone", "Pixel", "Galaxy", "OnePlus"],
    "thing": ["cat", "cricket", "cooking", "office", "exam", "gym"], "celebrity": ["a famous actor", "a popular YouTuber", "a pop star"],
    "situation": ["your mom finds your phone", "exam tomorrow", "first day at college"], "event": ["monsoon floods", "election results",
    "budget session", "metro expansion"], "city": ["Delhi", "Mumbai", "Bengaluru", "Kolkata"], "policy": ["education", "tax", "health"],
    "team": ["CSK", "MI", "RCB", "KKR"], "team2": ["GT", "SRH", "DC", "PBKS"], "player": ["Kohli", "Gill", "Pant"],
    "league": ["Premier League", "ISL", "La Liga"], "game": ["BGMI", "Valorant", "Minecraft", "Free Fire"], "brand": ["Myntra", "boAt", "Nykaa"],
}
EMOJIS = ["😂", "🔥", "😱", "💯", "📚", "🎉", "👀", "🤯"]


def _caption(category: ContentCategory, rng: random.Random) -> str:
    template = rng.choice(CAPTION_TEMPLATES[category])
    text = template.format(**{k: rng.choice(v) for k, v in FILL.items()})
    if rng.random() < 0.3:
        text += " " + rng.choice(EMOJIS)
    if rng.random() < 0.15:
        text = text.lower()
    if rng.random() < 0.08 and len(text) > 8:  # typo
        i = rng.randrange(1, len(text) - 2)
        text = text[:i] + text[i + 1] + text[i] + text[i + 2:]
    return text


def generate_captions(n_per_class: int, seed: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    rows = []
    categories = list(CAPTION_TEMPLATES)
    for category in categories:
        for _ in range(n_per_class):
            text = _caption(category, rng)
            if rng.random() < 0.1:  # ambiguous captions mixing a second topic
                text = f"{text} | {_caption(rng.choice(categories), rng)}"
            rows.append({"text": text, "label": category.value})
    rng.shuffle(rows)
    return rows


def _policy_ctx(weekday: int, minute: int, in_focus: bool, app: str, category: str, observed: ContentCategory, confidence: float,
                minutes: float, by_app: dict[str, float], by_cat: dict[str, float]) -> PolicyContext:
    return PolicyContext(weekday=weekday, minute_of_day=minute, focus_session_active=in_focus, app_package=app,
                         app_category=AppCategory(category), content_category=observed, content_confidence=confidence,
                         session_minutes=minutes, daily_minutes_by_app=by_app, daily_minutes_by_category=by_cat)


def simulate_persona(persona: Persona, seed: int, days: int) -> dict[str, list[dict[str, Any]]]:
    rules = ConstitutionCompiler().compile(persona.constitution).constitution.rules
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    history: deque[dict[str, Any]] = deque(maxlen=14)
    group = f"{persona.name}:{seed}"
    event_n = 0
    for day in range(days):
        plan = plan_day(persona, seed, day)
        day_start = BASE_DATE + timedelta(days=day)
        rng = stream(seed, day, 9_999)
        hourly = [0.0] * 24
        for s, e in plan.focus_blocks:
            for kind, minute, payload in (("FOCUS_STARTED", s, {"planned_minutes": e - s}), ("FOCUS_ENDED", e, {"completed": True})):
                event_n += 1
                out["events"].append({"persona": persona.name, "seed": seed, "client_event_id": f"syn-{seed}-{event_n:09d}",
                                      "event_type": kind, "occurred_at": (day_start + timedelta(minutes=minute)).isoformat(),
                                      "app_package": None, "payload": payload})
        sessions_total = sum(h["sessions"] for h in history)
        long_total = sum(h["long"] for h in history)
        minutes_total = sum(h["minutes"] for h in history)
        hist_hourly = [sum(h["hourly"][i] for h in history) for i in range(24)]
        hist_sum = sum(hist_hourly) or 1.0
        triggers = tuple(sorted(i for i in sorted(range(24), key=lambda i: -hist_hourly[i])[:3] if hist_hourly[i] >= 1.5 * hist_sum / 24))
        by_app: dict[str, float] = defaultdict(float)
        by_cat: dict[str, float] = defaultdict(float)
        opens: list[int] = []
        day_sessions = day_long = 0
        day_minutes = 0.0
        for session in plan.sessions:
            order = (session.start_minute - persona.wake_hour * 60) % 1440
            opens.append(order)
            minutes, checks = 0.0, []
            logged: dict[str, Any] | None = None
            for tick in range(MAX_TICKS):
                minute_of_day = (session.start_minute + int(minutes)) % 1440
                in_focus = plan.in_focus(minute_of_day)
                if tick > 0 and session.continue_draws[tick] >= continue_probability(
                        persona, minutes=minutes, minute_of_day=minute_of_day, content=session.content, in_focus=in_focus,
                        goal_relevant=session.goal_relevant):
                    break
                minutes += TICK_MINUTES
                hourly[minute_of_day // 60] += TICK_MINUTES
                by_app[session.app_package] += TICK_MINUTES
                by_cat[session.app_category] += TICK_MINUTES
                verdict = evaluate_policy(rules, _policy_ctx(plan.weekday, minute_of_day, in_focus, session.app_package,
                                                             session.app_category, session.observed_content,
                                                             session.observed_confidence, minutes, by_app, by_cat))
                features = featurize(RiskInput(
                    session_minutes=minutes, hour=minute_of_day // 60, minute=minute_of_day % 60, weekday=plan.weekday,
                    focus_active=in_focus, in_restricted_window=verdict.in_restricted_window and (verdict.effect is None or verdict.effect.value != "ALLOW"),
                    content_category=session.observed_content, content_confidence=session.observed_confidence,
                    app_category=AppCategory(session.app_category), opens_last_hour=sum(1 for t in opens if order - 60 <= t <= order + minutes),
                    daily_social_minutes=sum(by_cat.get(k, 0.0) for k in ("social_media", "video")), trigger_hours=triggers,
                    avg_session_minutes_14d=minutes_total / sessions_total if sessions_total else 0.0,
                    long_session_rate_14d=long_total / sessions_total if sessions_total else 0.0, history_days=len(history)))
                checks.append((minutes, in_focus, features))
                if logged is None and minutes >= 15 and rng.random() < LOGGING_RATE:
                    action = LOGGING_ACTIONS[int(rng.integers(0, len(LOGGING_ACTIONS)))]
                    outcome = respond(persona, action, draw=float(session.response_draws[tick]),
                                      override_draw=float(session.override_draws[tick]), same_today=0,
                                      goal_relevant=session.goal_relevant, in_focus=in_focus)
                    logged = {"group": group, "persona": persona.name, "seed": seed, "day": day, "session": session.index,
                              "minute_of_day": minute_of_day, "session_minutes": minutes, "in_focus": in_focus,
                              "content": session.content.value, "goal_relevant": session.goal_relevant, "action": action.value,
                              "propensity": LOGGING_RATE / len(LOGGING_ACTIONS), "outcome": outcome.value,
                              "reward": compute_reward(outcome)}
                    out["interventions"].append(logged)
            total = minutes
            unwanted = not session.goal_relevant and (total >= 30 or any(f for _, f, _ in checks))
            for at, _, features in checks:
                out["risk"].append({"group": group, "persona": persona.name, **{k: round(features[k], 5) for k in RISK_FEATURES},
                                    "label_distraction": int(unwanted), "label_doomscroll": int(total - at >= 20),
                                    "label_continuation": int(total - at >= 10)})
            day_sessions += 1
            day_long += total >= 30
            day_minutes += total
            start_at = day_start + timedelta(minutes=session.start_minute)
            out["sessions"].append({"group": group, "persona": persona.name, "seed": seed, "day": day,
                                    "start": start_at.isoformat(), "app_package": session.app_package,
                                    "app_category": session.app_category, "content": session.content.value,
                                    "goal_relevant": session.goal_relevant, "minutes": total, "long": total >= 30,
                                    "unwanted": unwanted})
            event_n += 1
            out["events"].append({"persona": persona.name, "seed": seed, "client_event_id": f"syn-{seed}-{event_n:09d}",
                                  "event_type": "APP_OPENED", "occurred_at": start_at.isoformat(),
                                  "app_package": session.app_package, "payload": {}})
            event_n += 1
            out["events"].append({"persona": persona.name, "seed": seed, "client_event_id": f"syn-{seed}-{event_n:09d}",
                                  "event_type": "APP_CLOSED", "occurred_at": (start_at + timedelta(minutes=total)).isoformat(),
                                  "app_package": session.app_package,
                                  "payload": {"duration_seconds": int(total * 60), "scroll_events": int(total * 6)}})
        history.append({"sessions": day_sessions, "long": day_long, "minutes": day_minutes, "hourly": hourly})
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(out_dir: Path, *, seeds: range, days: int, captions_per_class: int = 400) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for persona in PERSONAS:
        for seed in seeds:
            for key, rows in simulate_persona(persona, seed, days).items():
                merged[key].extend(rows)
    with (out_dir / "events.jsonl").open("w") as fh:
        for row in merged["events"]:
            fh.write(json.dumps(row) + "\n")
    _write_csv(out_dir / "sessions.csv", merged["sessions"])
    _write_csv(out_dir / "risk_training.csv", merged["risk"])
    _write_csv(out_dir / "interventions.csv", merged["interventions"])
    captions = generate_captions(captions_per_class, seed=7)
    _write_csv(out_dir / "content_captions.csv", captions)
    (out_dir / "goals.json").write_text(json.dumps([{"persona": p.name, "description": p.description, "constitution": p.constitution,
                                                     "style": p.style} for p in PERSONAS], indent=2))
    counts = {"events": len(merged["events"]), "sessions": len(merged["sessions"]), "risk_rows": len(merged["risk"]),
              "logged_interventions": len(merged["interventions"]), "captions": len(captions), "personas": len(PERSONAS),
              "seeds": len(seeds), "days": days}
    (out_dir / "manifest.json").write_text(json.dumps(counts, indent=2))
    return counts
