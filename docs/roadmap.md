# Roadmap

Ordered by what would most change the system's credibility, not by ease.

## 1. Evidence

- Run the Android app on real devices with consenting users; replace every simulated number with measured behaviour.
- Pre-register an A/B design (full system vs rule-only) with attention and wellbeing outcomes, not just minutes.
- Measure battery, data and CPU with Battery Historian and replace the placeholder.
- Revisit the bandit with a longer horizon and off-policy evaluation on logged propensities (already recorded in
  `interventions.csv`); drop it if it still does not pay for itself.

## 2. Product

- Server-initiated interventions via FCM, so protection does not depend on polling.
- On-device content classification (TFLite) to keep captions off the network entirely.
- Shared-accountability mode (a friend sees streaks, never content), and scheduled "deep work" calendars.
- Richer constitution language: exceptions per contact, per playlist, per course, with the compiler explaining every
  unsupported clause as it already does.

## 3. Platform

- Multi-device coordination with per-device capabilities and a single authoritative session view.
- iOS client using Screen Time APIs, where the capability set is narrower and the guardrail layer must adapt.
- Row-level security and per-tenant encryption for shared deployments.
- Model registry promotion flow: shadow scoring, canary, automatic rollback on calibration drift.

## 4. Research

- Causal estimation of intervention effects from logged propensities rather than simulator ground truth.
- Learned escalation policy with safety constraints, evaluated against the deterministic ladder.
- Fairness analysis across usage patterns and time zones once real data exists.
