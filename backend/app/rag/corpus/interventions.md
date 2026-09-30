---
title: How MindGuard interventions work
kind: system
---
# Intervention ladder
MindGuard chooses the gentlest intervention that is likely to work: ALLOW, SOFT_WARNING (a notification), MINDFUL_PROMPT (a short reflection question), REQUEST_CONFIRMATION (asks whether this is intentional), DELAY (a 2-5 minute pause before the app opens), LIMITED_ACCESS (a time box), TEMPORARY_BLOCK (at most 60 minutes) and FOCUS_MODE (a focus session the user starts).

# Why an intervention happened
Every intervention stores reason codes and a short explanation, for example: you are in a focus session, the session has lasted 32 minutes, your policy restricts short videos in this window, and your historical risk at this hour is high. MindGuard never shows hidden model reasoning, only these summaries.

# Authorization
Language models and the learning component only propose. A deterministic guardrail engine authorizes every action: it checks emergency override, paused guardian, essential apps, consent, allow-once, confidence, policy ceilings, style, device permissions, maximum durations, cooldowns and rate limits. Low confidence never results in a block; it becomes a warning or a question.

# Learning
After each intervention MindGuard records the outcome (accepted, overridden, ignored, stopped session). A contextual bandit learns which interventions work for you, but only inside the set of actions your policy allows. Overrides are treated as a signal to be gentler, never as permission to be stricter.
