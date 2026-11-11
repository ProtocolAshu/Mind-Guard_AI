#!/usr/bin/env python3
"""MindGuard portable reference engine (stateless, pure Python, no dependencies).

signals -> risk -> guardrails -> one action. The host app owns persistence,
logging, feedback and any learned policy (pass it in as `preferred_action`).
"""
import argparse, json, math

ACTIONS = ["ALLOW", "NUDGE", "DELAY", "FRICTION", "SOFT_BLOCK"]
EXEMPT = {"utility", "emergency", "maps", "navigation", "banking", "health", "work",
          "calls", "phone", "messages", "messaging", "payments", "wallet", "alarms"}
KNOWN = {"social", "video", "gaming", "news", "browser", "other_leisure"}
SOFT_BLOCKS_PER_DAY = 2
COOLDOWN_MIN = 10


def clip(x):
    return max(0.0, min(1.0, x))


def num(v, positive=False):
    """Finite, non-bool, non-negative number (or positive if asked); else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    if v < 0 or (positive and v == 0):
        return None
    return v


def clean(raw):
    """Validate signals. Returns (clean dict, set of fields that were present but invalid)."""
    raw = raw if isinstance(raw, dict) else {}
    c, bad = {}, set()
    for k in ("session_min", "scroll_per_min", "streak_days", "mins_since_last_intervention"):
        if k in raw:
            v = num(raw[k])
            c[k] = v
            if v is None:
                bad.add(k)
    if "user_limit_min" in raw:
        v = num(raw["user_limit_min"], positive=True)
        c["user_limit_min"] = v
        if v is None:
            bad.add("user_limit_min")
    if "hour" in raw:
        h = raw["hour"]
        ok = type(h) is int and 0 <= h <= 23
        c["hour"] = h if ok else None
        if not ok:
            bad.add("hour")
    if "recent_overrides" in raw:
        r = raw["recent_overrides"]
        ok = type(r) is int and r >= 0
        c["recent_overrides"] = r if ok else None
        if not ok:
            bad.add("recent_overrides")
    c["category"] = str(raw.get("category", "")).strip().lower()
    c["current_action"] = str(raw.get("current_action", "")).strip().upper()
    c["preferred_action"] = str(raw.get("preferred_action", "")).strip().upper()
    c["softblock_enabled"] = raw.get("softblock_enabled") is True  # strictly Boolean True
    c["softblocks_today"] = raw.get("softblocks_today")
    return c, bad


def is_night(s):
    h = s.get("hour")
    return 1.0 if h is not None and (h >= 23 or h < 5) else 0.0


def assess(s):
    """Return (risk, confidence) from validated signals (invalid or missing count as unknown)."""
    session = clip((s.get("session_min") or 0) / 60)
    scroll = clip((s.get("scroll_per_min") or 0) / 80)
    streak = clip((s.get("streak_days") or 0) / 7)
    limit = s.get("user_limit_min")
    over = clip((s.get("session_min") or 0) / limit - 1) if limit else 0.0
    risk = 0.35 * session + 0.25 * scroll + 0.15 * is_night(s) + 0.15 * streak + 0.10 * over
    keys = ["session_min", "scroll_per_min", "hour", "streak_days"]
    conf = sum(s.get(k) is not None for k in keys) / len(keys)
    return round(clip(risk), 3), round(conf, 2)


def rule_rung(risk):
    for i, t in enumerate([0.25, 0.45, 0.65, 0.85]):
        if risk < t:
            return i
    return 4


def cap_for(s, bad, conf, softblock_ok):
    """Highest action index the guardrails permit. Unknown or invalid state fails soft."""
    cat = s["category"]
    if cat in EXEMPT:
        return 0, "essential app is never restricted"
    cap, why = 4, "within limits"

    def lower(c, w):
        nonlocal cap, why
        if c < cap:
            cap, why = c, w

    if cat not in KNOWN:
        lower(1, "app category unknown or unclassified")
    if bad:
        lower(1, "invalid signal values: " + ", ".join(sorted(bad)))
    if conf < 0.5:
        lower(1, "low confidence")
    if (s.get("recent_overrides") or 0) >= 2:
        lower(1, "repeated overrides")
    mins = s.get("mins_since_last_intervention")
    if mins is None:
        lower(1, "cooldown history unknown")
    elif mins < COOLDOWN_MIN:
        lower(0, "cooldown active")
    prev = s["current_action"]
    if prev in ACTIONS:
        lower(min(ACTIONS.index(prev) + 1, 4), "escalate one rung at a time")
    else:
        lower(1, "previous action unknown")
    count = s["softblocks_today"]
    consent_valid = softblock_ok is True and s["softblock_enabled"] is True
    count_valid = type(count) is int and 0 <= count < SOFT_BLOCKS_PER_DAY
    if not (consent_valid and count_valid):
        lower(3, "SOFT_BLOCK requires explicit consent and a valid daily count under the limit")
    return cap, why


def decide(raw, softblock_ok=False):
    s, bad = clean(raw)
    risk, conf = assess(s)
    rung = rule_rung(risk)
    cap, why = cap_for(s, bad, conf, softblock_ok)
    allowed = list(range(max(0, min(rung, cap) - 1), min(cap, rung + 1) + 1))
    pref = s["preferred_action"]  # optional: host's learned policy
    if pref in ACTIONS and ACTIONS.index(pref) in allowed:
        a = ACTIONS.index(pref)
    else:
        a = min(rung, cap)
    return {"risk": risk, "confidence": conf,
            "allowed_actions": [ACTIONS[i] for i in allowed], "action": ACTIONS[a],
            "reason": f"Risk {risk} (confidence {conf}); guardrail limit: {why}."}


DEMO = [
    {"session_min": 5, "scroll_per_min": 10, "hour": 15, "streak_days": 0, "category": "social",
     "current_action": "ALLOW", "mins_since_last_intervention": 120},
    {"session_min": 55, "scroll_per_min": 65, "hour": 14, "streak_days": 3, "category": "social",
     "current_action": "NUDGE", "mins_since_last_intervention": 30},
    {"session_min": 90, "scroll_per_min": 75, "hour": 2, "streak_days": 6, "category": "calls",
     "current_action": "ALLOW", "mins_since_last_intervention": 60},
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", help="JSON string of signals")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--allow-soft-block", action="store_true",
                    help="user has consented to SOFT_BLOCK (also needs softblock_enabled in signals)")
    a = ap.parse_args()
    for s in ([json.loads(a.signals)] if a.signals else DEMO):
        print(json.dumps(decide(s, a.allow_soft_block)))
