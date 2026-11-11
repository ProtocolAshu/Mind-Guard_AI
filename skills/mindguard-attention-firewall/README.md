# MindGuard Attention Firewall (agent skill)

A portable decision skill: usage signals → risk → guardrails → one explainable, reversible intervention.

## Implementation scope

This package provides a portable reference implementation of MindGuard's attention decision pipeline. It demonstrates risk assessment, intervention selection, guardrail enforcement and explainable decisions.

The standalone script is stateless and does not replace MindGuard's full multi-agent orchestration, trained ML models, Android monitoring or backend learning infrastructure. It does not store or delete logs, persist a learned policy, or monitor any device: the host application handles those. Actual device monitoring and enforcement require the full MindGuard application or an authorized integration.

## Contents
`SKILL.md` (the skill), `references/` (framework and guardrails), `scripts/decision_engine.py` (pure Python, no dependencies), `examples/sample-decisions.md`, `tests/` (`python -m unittest discover -s tests`).

## Quick start
`python scripts/decision_engine.py --demo`
