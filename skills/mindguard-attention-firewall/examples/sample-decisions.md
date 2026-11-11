# Sample decisions

**1. Normal use → ALLOW**
Signals: social app, 5 min, 10 scrolls/min, 3 pm, no streak, previous action ALLOW.
Result: risk 0.06, action ALLOW. Nothing to do; the user is within their limits.

**2. Prolonged scrolling during the day → DELAY**
Signals: social app, 55 min, 65 scrolls/min, 2 pm, 3-day streak, previous action NUDGE, last intervention 30 min ago.
Result: risk 0.588, allowed NUDGE or DELAY, action DELAY (one rung above NUDGE). Reason shown to the user in one line, with a one-tap override.

**3. Emergency call at 2 am → ALLOW regardless of risk**
Signals: category calls, 90 min, 75 scrolls/min, 2 am, 6-day streak.
Result: risk 0.863 but action ALLOW; essential apps are never restricted.

**4. Late-night spiral with consent → FRICTION, SOFT_BLOCK only if allowed**
With previous action FRICTION, softblock_enabled true and softblocks_today 0, plus `--allow-soft-block`, SOFT_BLOCK is permitted. With softblocks_today 2 or unknown, the engine stays at FRICTION.
