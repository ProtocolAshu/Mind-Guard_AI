---
name: mindguard-attention-firewall
description: Use when an AI agent needs to evaluate social-media usage signals against user-defined attention rules, calculate distraction or doomscrolling risk, enforce autonomy and privacy guardrails, recommend a reversible intervention, or explain an attention-management decision. Trigger on MindGuard, attention firewall, screen-time control, digital wellbeing or intervention-policy tasks.
summary: A portable AI attention firewall for personalized digital wellbeing, distraction detection, and explainable interventions.
version: 1.0.0
tags: [productivity, digital-wellbeing, screen-time, focus, attention-management]
docs_url: https://github.com/ProtocolAshu/Mind-Guard_AI
keywords: [attention management, doomscrolling prevention, social media control, digital wellness, distraction management, mindful productivity]
use_cases: [Manage social media usage, Enforce personalized focus rules, Detect prolonged scrolling, Recommend mindful interventions, Improve digital wellbeing]
---

# MindGuard Attention Firewall: decision skill

Turns usage signals into one justified, reversible intervention. See README.md for scope and limitations.

## Pipeline

1. **Sense**: gather metadata signals (see `references/decision-framework.md`). Never read message or post content.
2. **Assess risk**: 0-1 risk score plus confidence. Low confidence means act softer.
3. **Guardrail check**: apply `references/guardrails.md`. Guardrails are hard limits and always beat any learned or preferred policy.
4. **Choose**: pick the least intrusive effective action from the allowed set. A host's learned policy (e.g. a contextual bandit) can be supplied as `preferred_action`; it is used only if allowed.
5. **Explain**: give the user a one-line reason. The host app stores logs, handles feedback and learning.

## Intervention ladder

`ALLOW` → `NUDGE` → `DELAY` → `FRICTION` → `SOFT_BLOCK`

Escalate at most one rung above the previous action. De-escalate promptly when risk drops or the user overrides.

## Rules of thumb

- User-declared limits and focus windows beat generic thresholds.
- Essential apps (calls, messaging, maps, payments, banking, health, alarms, work) are never restricted.
- Unknown state (category, previous action, cooldown history, soft-block count) fails soft: ALLOW or NUDGE only.
- Every action is explainable in one sentence and reversible in one tap.

## Using the engine

```bash
python scripts/decision_engine.py --demo
python scripts/decision_engine.py --signals '{"session_min":55,"scroll_per_min":65,"hour":14,"streak_days":3,"category":"social","current_action":"NUDGE","mins_since_last_intervention":30}'
```

Add `--allow-soft-block` (plus `softblock_enabled` and `softblocks_today` in the signals) only when the user has consented to SOFT_BLOCK. Pure Python, no dependencies. Tests: `python -m unittest discover -s tests`. Worked scenarios: `examples/sample-decisions.md`.
