# MindGuard: An Autonomous Multi-Agent AI Attention Firewall for Personalized Social-Media Control

*Technical report. Every quantitative result below comes from a synthetic simulator written by the authors; no real
user has used this system. Claims that would require human subjects are marked `[PLACEHOLDER]`.*

## 1. Abstract

Screen-time tools fail for a simple reason: they cannot tell the difference between a lecture and a meme reel, and
they punish both with the same blunt timer. MindGuard is an attention firewall that reasons about *context* —
the user's own goals and rules, the current session, the content category, and a learned behavioural profile — and
chooses the lightest intervention that fits. It is built as nine cooperating agents on a LangGraph orchestration
with a deterministic guardrail layer that authorises, softens or rejects every proposal, including proposals made by
a language model. In a simulation of five personas over 21 days with 10 seeds, the full system reduced "unwanted"
social-media minutes from 17.1 to 8.35 per day against no intervention (p = 1.2e-07 after Holm correction), and beat
a static daily limit by 4.7 minutes per day while being overridden a fifth as often (0.11 vs 0.60). An ablation shows
that the calibrated risk model and contextual features carry that improvement, while the contextual bandit did not
pay for itself within the evaluated horizon and the memory layer showed no measurable effect. Under injected LLM
failures — errors, timeouts, malformed output and output attempting to disable protection — 104 evaluation runs all
returned valid, safe decisions.

## 2. Introduction

Attention is the scarce resource in a phone-mediated life, and the applications competing for it are optimised by
teams with far better feedback loops than any individual's willpower. Existing countermeasures are either blunt
(app timers, greyscale, "focus modes") or paternalistic (parental controls), and both share a failure mode: they
model *time*, not *intent*. A student watching a data-structures lecture and a student watching short comedy videos
are indistinguishable to a timer, yet only one of them is being harmed by the session.

MindGuard asks a different question: given what this person said they want, what they are doing right now, and what
has actually worked for them before, what is the smallest action that helps? The answer must be explainable
(the user should see why), reversible (the user remains in charge), and safe (a language model must never be able
to remove someone's protection). This report describes the system, its evaluation, and — as prominently — what it
does not yet demonstrate.

## 3. Problem statement

Given a stream of device usage events, a user-authored set of goals and rules in natural language, and optional
content metadata, decide at each moment whether to intervene and how, such that:

1. time spent against the user's own stated intent decreases;
2. interventions that the user rejects (overrides) are rare, because rejection destroys the tool's legitimacy;
3. useful activity is not restricted (low false-positive rate);
4. every decision is explainable and reversible; and
5. the system degrades safely when its components fail.

The objective is explicitly *not* to minimise screen time. It is to minimise the gap between how someone wanted to
spend their attention and how they did.

## 4. Motivation

Three observations shaped the design. First, the same intervention has wildly different effects on different people:
a hard block produces compliance in one person and defiance in another, so a fixed escalation ladder is wrong for
somebody. Second, an intervention that arrives while a user is doing something they consider valuable is worse than
no intervention at all, because it teaches them to disable the tool. Third, any system that reasons with a language
model over content drawn from social media is a prompt-injection target: the content is written by parties with an
interest in the tool's failure.

## 5. Related work

Four strands are relevant. **Digital wellbeing tooling** (Android Digital Wellbeing, iOS Screen Time, Freedom,
one-tap blockers) provides time budgets and schedules without content or goal awareness. **Behaviour-change
research** on just-in-time adaptive interventions (JITAI) formalises the idea of intervening at moments of
receptivity, and supplies the vocabulary of tailoring variables and decision points used here. **Contextual bandits
and reinforcement learning for mobile health** (LinUCB, Thompson sampling, micro-randomised trials) provide the
personalisation machinery and, importantly, the warnings about exploration cost in short horizons. **LLM agent
safety** work on prompt injection, tool sandboxing and structured output validation motivates the architecture's
central rule: the model proposes, a deterministic engine disposes. This report makes no novelty claim against these
literatures; it is an engineering synthesis with an honest evaluation.

## 6. Limitations of existing solutions

| Approach | Limitation |
|---|---|
| App timers and schedules | Content-blind and goal-blind; a lecture and a meme reel are the same minute |
| Hard blockers | High override and uninstall rates; adversarial relationship with the user |
| Notification batching | Addresses interruption, not self-initiated scrolling, which is the larger share |
| Screen-time dashboards | Retrospective; no action at the decision point |
| LLM "coach" apps | Unbounded model authority, weak or absent guardrails, prompt-injection surface, opaque reasoning |

## 7. Proposed system

MindGuard evaluates a moment through a fixed pipeline: context → goals → content → behaviour → policy → risk →
decision → guardrails → action. Users write rules in natural language ("don't allow short videos during study
sessions, but educational YouTube is fine"); a deterministic compiler turns them into a typed rule DSL and reports
exactly what it could not express. A calibrated behavioural model estimates distraction, doomscrolling and
continuation probabilities. A decision ladder maps risk band and rule effect to a recommendation and a set of
eligible actions. A language model is consulted only when the deterministic signal is ambiguous, may call three
read-only tools, and produces a *proposal*. Fourteen guardrail checks then authorise, soften or reject it. The device
validates the resulting command again before acting.

## 8. System architecture

FastAPI backend, PostgreSQL 16 with pgvector, optional Redis, an Android client and a Next.js dashboard. 70 API
operations across 60 paths; 26 tables with migrations; 13 typed tools; every agent step, tool call and model call is
recorded with latency, status and cost, so any decision can be replayed. Six component interfaces (LLM, vision,
embeddings, behaviour model, policy engine, intervention controller) are consumed through a dependency container and
are swap-tested, so no vendor or model is load-bearing. Details: `docs/architecture.md`.

## 9. Agent architecture

Nine agents: Supervisor (routing, including short paths for emergency override and essential apps), Context, Goal,
Content, Behavior, Policy, Risk, Decision, Action, plus a Learning agent on the feedback graph. Agents that must be
reproducible and cheap are deterministic; their prompts remain the authoritative contract and are exposed read-only
for transparency. The LLM-callable surface is deliberately tiny: three read-only tools, at most two tool turns, no
write or device authority. Measured trajectory: 8.25 nodes and 7.06 tool calls per evaluation.

## 10. LLM architecture

A gateway provides model tiering (a fast model for classification, a stronger model for ambiguous decisions), strict
JSON schema validation, response caching, per-user daily token budgets, a monthly USD budget, timeouts, bounded
retries with jittered backoff and a fallback model. Five failure modes are handled identically: fall back to
deterministic logic, flag `LLM_FALLBACK`, mark the run degraded, and tell the user that rules decided. A model that
returns a decision outside the eligible set, or that attempts to change policy or consent, is rejected by
construction rather than by prompt instruction.

## 11. Behavioral ML

23 features spanning session, time-of-day, intent, content, app and 14-day history, computed by one function for both
training and serving. Labels come from the simulator. Models are selected on a grouped validation split (no
simulated user appears in two splits) and calibrated with isotonic regression. Test results: distraction ROC-AUC
0.877 with expected calibration error 0.022 (rules baseline 0.799 / 0.063); doomscroll 0.634 (0.573); continuation
0.605 (0.559); content classification macro-F1 0.948 against a 0.751 lexicon baseline. Serving blends the calibrated
model with the interpretable rules (weight 0.6) so that every score still comes with human-readable factors, and a
model failure degrades to rules rather than to an exception.

## 12. Memory and RAG

Four memory types (semantic, episodic, preference, insight) with importance and expiry, embedded into a pgvector
HNSW cosine index. A small knowledge corpus grounds the constitution compiler and keeps platform claims accurate —
for instance, it states what Android permissions actually allow, so explanations never promise capabilities the
device does not have. Preference memories are written only when a statistical test over outcomes supports them.
All memory is consent-gated, inspectable and deletable from the dashboard.

## 13. Contextual bandits

LinUCB (with epsilon-greedy and Thompson alternatives) over a 16-dimension context chooses among the *eligible*
actions the ladder allows, never outside it. Rewards are derived from outcomes: accepted +1, ignored −0.2,
overridden −1, with partial credit for stopping a session. Propensities are logged for later off-policy analysis.
The evaluation below is unflattering to this component, and the system is designed so it can be switched off
(`D_no_bandit`) without changing anything else.

## 14. Privacy

Seven consent scopes, all off by default. Raw content is never stored; captions and screenshots are analysed in
memory and discarded. Agent traces are summarised with secrets and text redacted. Data export and two deletion
scopes are first-class API operations, surfaced in the dashboard. The Android app requests no location, contacts,
camera, microphone or accessibility permissions, excludes itself from cloud backup, and shows a persistent
notification whenever monitoring runs — there is no silent mode by design.

## 15. Security

Argon2id passwords, rotating refresh tokens with family revocation on reuse, HMAC-signed device command tickets
bound to user, run and app, a hash-chained audit log verified on demand, per-route rate limits, body size limits and
a strict content security policy. Untrusted content is sanitised, wrapped and quarantined on injection detection —
and never forwarded to a model once detected. The dashboard uses a backend-for-frontend so the browser never holds a
token. All ten required security test cases are automated, alongside 23 end-to-end checks of the session and proxy
layer.

## 16. Android architecture

Ten Kotlin modules with exactly one third-party dependency (coroutines). A foreground service polls usage statistics,
reconstructs sessions with deterministic event ids, and asks the backend for a decision at most every two minutes per
session. Interventions are shown as notifications or, when permitted, as an overlay that always offers "Allow once"
and "Emergency". Tokens are encrypted with a non-exportable Keystore key. An outbox with exponential backoff survives
offline periods; offline, cached rules may only *remind*, never restrict, because restrictions require server-side
guardrail authorisation. Device-side validation rejects expired, over-long, irreversible or essential-app commands
even if the server sent them.

## 17. Backend architecture

Async FastAPI with a dependency container, uniform error envelope, request-id propagation, OpenTelemetry spans and
Prometheus metrics. Agent runs, steps, tool calls and model calls are persisted for replay; an admin endpoint re-runs
a recorded decision inside a rolled-back savepoint and reports whether the outcome changed. Event ingestion is
idempotent per `(user, client_event_id)`. Daily behaviour features are pre-aggregated for analytics. Migrations are
checked for drift against the models in CI.

## 18. Experimental methodology

A discrete-event simulator generates impulses, sessions, content and responses for five personas (night-owl student,
focused-but-impulsive, reactant scroller, compliant planner, binge watcher) parameterised by impulsivity, peak hours,
binge tendency, commitment, reactance, habituation and per-intervention affinity. Common random numbers ensure every
controller faces the identical impulse stream for a given (persona, seed). Ten controllers were run over 5 personas ×
10 seeds × 21 days, discarding the first 7 days as a learning warm-up: four baselines (no intervention, static daily
limit, reminder only, rule-based), five ablations (no memory, no context, no personalisation, no bandit, rule-only
risk) and the full system. Primary metric: "unwanted" minutes per day, meaning time in sessions the persona's own
goals classify as unwanted. Secondary: goal adherence, override rate, false-positive rate, intervention count and a
satisfaction proxy. Statistics: paired Wilcoxon signed-rank over matched episodes, Holm correction across all tests,
bootstrap 95% confidence intervals, rank-biserial effect sizes. Separately, an agent evaluation ran 8 scenarios × 5
LLM failure modes against the real graph with the served models.

## 19. Evaluation

Three questions: (a) does the system reduce unwanted use relative to plausible baselines; (b) which components earn
their place; (c) does the agent machinery behave safely under failure and adversarial content. Question (a) is
answered by the baseline comparison, (b) by the ablations, and (c) by fault injection and the security suite. Every
result is conditional on the simulator's user model, which is an assumption rather than data.

## 20. Results

| Controller | Unwanted min/day [95% CI] | Override rate | False positives | Satisfaction |
|---|---|---|---|---|
| No intervention | 17.11 [14.29, 19.95] | — | — | 0.00 |
| Static daily limit | 13.09 [10.46, 15.93] | 0.60 | 0.25 | −0.45 |
| Reminder only | 14.15 [11.91, 16.31] | 0.00 | 0.26 | −0.18 |
| Rule-based | 13.57 [11.19, 16.03] | 0.46 | 0.11 | −0.24 |
| **Full system** | **8.35 [6.59, 10.26]** | **0.11** | **0.08** | **−0.08** |

Paired differences (full system minus baseline, Holm-corrected): −8.76 min/day vs no intervention
(p = 1.2e-07, r = −1.00), −4.74 vs static limit (p = 1.9e-06, r = −0.95), −5.80 vs reminder only (p = 4.8e-07),
−5.22 vs rule-based (p = 3.0e-05). Goal adherence improves by 0.016 against no intervention (p = 7.6e-07). The
override rate is 0.49 lower than a static limit (p = 8.6e-13) and the satisfaction proxy is 0.37 higher
(p = 2.0e-12).

Agent and system evaluation (104 runs): task success 1.00; valid response rate 1.00 under every injected LLM failure
mode; 629 tool calls with zero invalid calls; latency p50 103 ms and p95 661 ms (the tail is injected timeouts);
Python heap peak 10.1 MB. Battery impact: `[PLACEHOLDER — requires on-device measurement]`.

## 21. Ablation study

| Removed | Δ unwanted min/day (full − ablation) | p (Holm) | Effect size |
|---|---|---|---|
| Rule-only risk (no ML) | −2.51 | 0.0006 | r = −0.78 |
| No context | −0.99 | 0.042 | r = −0.62 |
| No personalisation | −0.44 | 0.56 | r = −0.46 |
| No memory | −0.16 | 1.0 | r = −0.02 |
| No bandit | **+0.94** | 1.0 | r = +0.40 |

Two components justify themselves on this evidence: the calibrated risk model and the contextual features. The
contextual bandit does not — its point estimate is *worse* than the deterministic ladder, though the difference is
not significant after correction, which is consistent with exploration cost dominating personalisation benefit over
14 evaluated days. Memory and personalisation show no measurable effect at this horizon. The honest conclusion is
that MindGuard's gains come from context-aware, calibrated risk estimation plus a well-ordered escalation ladder, and
that the learning layer remains unproven.

## 22. Limitations

The user model is an assumption; personas were written by the same authors who designed the system, which is a
structural bias no amount of statistics repairs. "Unwanted minutes" and "satisfaction" are simulator constructs, not
validated instruments. The evaluated horizon (21 days, 14 scored) is short for a learning system. Doomscroll and
continuation models are weak (ROC-AUC 0.634 and 0.605). No real device measurements exist: battery, network and CPU
impact are placeholders, and OEM background-process restrictions — the usual cause of such apps failing in the field
— are untested. No live commercial LLM was called during evaluation; the mock provider is deterministic and
therefore easier than reality. No penetration test, load test or fuzzing campaign has been run. Finally, an
attention firewall can only help someone who wants one: nothing here addresses the underlying reasons a person
reaches for a phone.

## 23. Ethical considerations

The system is built for self-governance, not surveillance: it monitors only the account holder, requires explicit
consent per data category, shows a persistent notification whenever it is watching, and never restricts calls,
messages, maps, payments or alarms. Every restriction is reversible within one tap, which is a deliberate rejection
of "commitment device" designs that trap users. Autonomy is treated as a constraint rather than an obstacle: the
learning layer exists partly to *reduce* the strength of interventions when gentler ones work. Risks remain —
a coercive party could install such an app on someone else's phone, and framing attention as a number can itself
become a source of anxiety; the design mitigates the first only through visibility, and the second by presenting
transparent, explainable components rather than an opaque score. Any real-world deployment should go through ethics
review before data collection, and the research consent scope is off by default.

## 24. Future work

Real-device deployment and a pre-registered A/B study; off-policy evaluation using the logged propensities already
recorded; a longer-horizon re-test of the bandit (and its removal if it still does not pay); on-device content
classification so captions never leave the phone; push-initiated interventions; an iOS client under the narrower
Screen Time API; and fairness analysis once real usage data exists. See `docs/roadmap.md`.

## 25. Conclusion

MindGuard demonstrates that a context-aware, multi-agent attention firewall can be built with strong safety
properties: a language model that can only propose, a deterministic guardrail layer that decides, explanations for
every action, reversibility everywhere, and graceful degradation under every failure mode we injected. In simulation
it halves unwanted social-media time relative to no intervention and clearly beats blunt baselines on both minutes
and user-acceptance proxies. The same evaluation declines to support two of its own components: the bandit and the
memory layer did not earn their place within the horizon tested. That combination — a working, safe, explainable
system and an evaluation that contradicts part of its own design — is the honest state of this project.

## 26. References

1. Nahum-Shani, I. et al. *Just-in-Time Adaptive Interventions (JITAIs) in Mobile Health.* Annals of Behavioral Medicine, 2018.
2. Li, L., Chu, W., Langford, J., Schapire, R. *A Contextual-Bandit Approach to Personalized News Article Recommendation.* WWW, 2010.
3. Chapelle, O., Li, L. *An Empirical Evaluation of Thompson Sampling.* NeurIPS, 2011.
4. Klasnja, P. et al. *Micro-Randomized Trials: An Experimental Design for Developing Just-in-Time Adaptive Interventions.* Health Psychology, 2015.
5. Hiniker, A., Hong, S., Kohno, T., Kientz, J. *MyTime: Designing and Evaluating an Intervention for Smartphone Non-Use.* CHI, 2016.
6. Kovacs, G., Wu, Z., Bernstein, M. *Rotating Online Behavior Change Interventions Increases Effectiveness But Also Increases Attrition.* CSCW, 2018.
7. Guo, C., Pleiss, G., Sun, Y., Weinberger, K. *On Calibration of Modern Neural Networks.* ICML, 2017.
8. Niculescu-Mizil, A., Caruana, R. *Predicting Good Probabilities with Supervised Learning.* ICML, 2005.
9. Chen, T., Guestrin, C. *XGBoost: A Scalable Tree Boosting System.* KDD, 2016.
10. Ke, G. et al. *LightGBM: A Highly Efficient Gradient Boosting Decision Tree.* NeurIPS, 2017.
11. Greshake, K. et al. *Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec, 2023.
12. Yao, S. et al. *ReAct: Synergizing Reasoning and Acting in Language Models.* ICLR, 2023.
13. Schick, T. et al. *Toolformer: Language Models Can Teach Themselves to Use Tools.* NeurIPS, 2023.
14. Holm, S. *A Simple Sequentially Rejective Multiple Test Procedure.* Scandinavian Journal of Statistics, 1979.
15. Efron, B., Tibshirani, R. *An Introduction to the Bootstrap.* Chapman & Hall, 1993.
16. Malkov, Y., Yashunin, D. *Efficient and Robust Approximate Nearest Neighbor Search Using Hierarchical Navigable Small World Graphs.* TPAMI, 2018.
17. Deci, E., Ryan, R. *Self-Determination Theory.* Psychological Inquiry, 2000. (Autonomy as a design constraint.)
18. Brehm, J. *A Theory of Psychological Reactance.* Academic Press, 1966. (Why hard blocks provoke overrides.)
