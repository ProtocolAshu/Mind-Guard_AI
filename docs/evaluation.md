# Evaluation

Two evaluations: a **simulation study** comparing controllers on synthetic users, and an **agent/system evaluation**
measuring the agent machinery itself. Everything below was produced by the scripts in `research/` and can be
reproduced with `make research`.

> All results come from a synthetic simulator written by the authors. They describe the behaviour of the decision
> logic under stated assumptions and are **not** evidence of real-world efficacy.

## Simulation study

**Protocol.** 10 controllers × 5 personas × 10 seeds × 21 days, common random numbers across controllers (the same
persona-seed faces the same impulse stream), the first 7 days discarded as learning warm-up. Statistics: paired
Wilcoxon signed-rank over matched (persona, seed) episodes, Holm correction across all tests, bootstrap 95%
confidence intervals, rank-biserial effect sizes. Personas: night-owl student, focused-but-impulsive, reactant
scroller, compliant planner, binge watcher, each with its own compliance, reactance and habituation parameters.

**Controllers.** Baselines B1 no intervention, B2 static daily limit, B3 reminder only, B4 rule-based; ablations
A no memory, B no context, C no personalisation, D no bandit, E rule-only risk; F full system.

| Controller | Unwanted min/day [95% CI] | Override rate | False-positive rate | Satisfaction |
|---|---|---|---|---|
| B1 no intervention | 17.11 [14.29, 19.95] | n/a | n/a | 0.00 |
| B2 static limit | 13.09 [10.46, 15.93] | 0.60 | 0.25 | −0.45 |
| B3 reminder only | 14.15 [11.91, 16.31] | 0.00 | 0.26 | −0.18 |
| B4 rule-based | 13.57 [11.19, 16.03] | 0.46 | 0.11 | −0.24 |
| **F full system** | **8.35 [6.59, 10.26]** | 0.11 | 0.08 | −0.08 |

Against every baseline the full system reduces unwanted minutes with large effect sizes and survives Holm
correction: −8.76 vs B1 (p = 1.2e-07, r = −1.00), −4.74 vs B2 (p = 1.9e-06), −5.80 vs B3 (p = 4.8e-07), −5.22 vs B4
(p = 3.0e-05). It does so while being overridden far less than static limits (0.11 vs 0.60) and with a better
satisfaction proxy than every intervening baseline.

## Ablations, read honestly

| Removed component | Δ unwanted min/day | p (Holm) | Reading |
|---|---|---|---|
| E rule-only risk (no ML) | −2.51 in favour of full | 0.0006 | The trained risk model contributes materially |
| B no context | −0.99 in favour of full | 0.042 | Context helps, modestly |
| C no personalisation | −0.44 | 0.56 | No measurable effect in 14 evaluated days |
| A no memory | −0.16 | 1.0 | No measurable effect |
| **D no bandit** | **+0.94 against full** | 1.0 | Removing the bandit was *slightly better*, not significant |

The contextual bandit did **not** pay for itself in this simulation: within a 21-day horizon its exploration cost
roughly cancels its personalisation benefit, and the point estimate favours the deterministic ladder. Memory and
personalisation likewise show no measurable effect at this horizon. Only the ML risk model and context earn their
place on this evidence. These are results about a simulator, and a longer horizon or a real population could change
any of them — but nothing here justifies claiming that the bandit or the memory layer improves outcomes.

Figures: `research/figures/` (controller comparison, persona heat map, learning curves, intervention mix).

## Agent and system evaluation

`research/agent_eval.py`: 8 scenarios × 5 LLM failure modes (healthy, error, timeout, malformed, compromised),
104 runs against the real graph with the served models and an in-memory database.

| Metric | Result |
|---|---|
| Task success (healthy LLM / all modes) | 1.00 / 1.00 |
| Valid response rate under fault injection | 1.00 (every mode) |
| Degraded-run rate | 0.125, exactly the runs that reached the LLM |
| Tool calls | 629, success 1.00, invalid 0.00 |
| LLM-requested tool calls | 5, of which **5 changed nothing** |
| Trajectory | 8.25 nodes, 7.06 tool calls per evaluation |
| Latency | p50 103 ms, p95 661 ms (p95 driven by injected timeouts) |
| Memory | 10.1 MB Python heap peak, 269 MB process RSS |
| LLM path exercised | 25% of runs |
| Battery impact | **[PLACEHOLDER — requires on-device measurement]** |

The "unnecessary tool call" finding is worth stating plainly: in this configuration the model's tool use added
latency without changing a single decision.

## What has not been evaluated

No real users, no A/B test, no longitudinal study, no clinical or wellbeing outcome, no battery or network
measurement on a physical device, and no evaluation with a live commercial LLM. The simulator's user model is an
assumption, not data. Sections 18–22 of the [research report](research-report.md) state these limits in full.

## Reproduce

```bash
make experiments   # resumable grid; rerun until it reports 0 remaining, then it analyses
make research      # the above plus the agent/system evaluation
python demo/run_demo.py   # the six specification scenarios, self-checking
```
