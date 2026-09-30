# MindGuard

An autonomous multi-agent AI attention firewall: it watches how you actually use social media, compares that with
goals and rules **you** wrote in plain language, and intervenes with the lightest action that fits — a heads-up, a
mindful check-in, a short delay, or a pause you can always override.

The system is a nine-agent LangGraph orchestration on FastAPI, a behavioural ML risk model, a contextual bandit for
personalisation, a deterministic policy DSL with a 14-step guardrail layer, an Android client, and a Next.js
dashboard. Every decision is explainable, reversible and recorded in a hash-chained audit log.

```
Android app  ──events──►  FastAPI  ──►  Supervisor ─► Context ─► Goal ─► Content ─► Behavior ─► Policy ─► Risk
   ▲                         │                                                                            │
   └──── device command ─────┴──────────── Action ◄── Guardrails ◄── Decision (LLM only when uncertain) ◄──┘
                                              │
                                        Feedback ─► Learning ─► Memory (pgvector)
```

## What is in here

| Path | What it is |
|---|---|
| `backend/` | FastAPI service: 9 agents, 13 tools, policy DSL + guardrails, risk scoring, bandits, memory/RAG, 70 API operations, 26 tables, Alembic migrations |
| `android/` | Kotlin app in 10 modules (framework + coroutines only): usage monitoring, foreground guardian service, overlay/notification interventions, encrypted token store, offline outbox |
| `web/` | Next.js 15 dashboard: 13 product screens, admin agent console with trace replay, backend-for-frontend auth (httpOnly cookies, CSRF, path validation) |
| `ml/` | Synthetic dataset generation, risk and content model training, calibrated evaluation, SHA-256-verified model registry |
| `simulator/` | Five behavioural personas, common-random-numbers world, four baselines and six ablation controllers |
| `research/` | Resumable experiment grid, bootstrap CIs, Wilcoxon + Holm statistics, agent/system evaluation, figures |
| `demo/` | The six specification demo scenarios, narrated and self-checking |
| `docs/` | Architecture, agents, API, database, security, privacy, ML, evaluation, Android, deployment, setup, troubleshooting, limitations, research report |
| `infra/`, `.github/` | Dockerfiles, docker-compose (PostgreSQL + pgvector, Redis), GitHub Actions CI |

## Quick start (no Docker, no API keys)

New to this download? [GETTING_STARTED.md](GETTING_STARTED.md) covers unzip → VS Code → GitHub → free
deployment end to end. The below is the bare local-dev loop.

```bash
make install                 # backend dependencies (Python 3.12)
cd backend && alembic upgrade head   # or let SQLite auto-create for local play
make api                     # http://localhost:8000/docs
make install-web && make web # http://localhost:3000
```

The default configuration runs **fully offline**: a deterministic mock LLM, a hashing embedder, and the trained
local risk/content models. Set `LLM_PROVIDER=anthropic` and `LLM_API_KEY=…` to use a real model.

```bash
make demo        # the six demo scenarios against the real agent graph
make seed        # synthetic users for the admin console
make test-all    # lint, types, security scan, backend and web tests
docker compose up --build    # PostgreSQL + pgvector, Redis, API, dashboard
```

## Design decisions worth knowing

- **The LLM never decides alone.** It is consulted only when deterministic risk is ambiguous, it may call three
  read-only tools, and its proposal is authorised, softened or rejected by a deterministic guardrail engine. If it
  errors, times out, returns malformed JSON or tries to change policy, the run degrades to rule-based logic.
- **Content the user did not write is untrusted.** Captions, titles and transcripts are sanitised, wrapped in
  `<untrusted_*>` blocks, and quarantined when injection is detected — detected content is never sent to a model.
- **Restrictions are always reversible.** Every restriction carries "Allow once" and "Emergency", essential apps
  (calls, messages, maps, payments, alarms) are never restricted, and the device validates commands again before
  acting.
- **Consent gates everything.** Nothing is collected or analysed without an explicit scope, and all data can be
  exported or deleted.

## Verification status

Everything below was executed in the build environment; the numbers come from those runs. Rows marked ↻ were
independently re-run in a later verification pass (fresh container, dependencies installed from scratch) and two
real bugs were found and fixed there: a Redis-backed rate-limit test that leaked state across the suite (fixed by
resetting it before the test), and a pinned `next` version with a disclosed unauthenticated RCE (patched to a fixed
15.x release). See the git history for details.

| Check | Result |
|---|---|
| Backend tests (SQLite) ↻ | 175 passed, 6 skipped |
| Backend tests (PostgreSQL 16 + pgvector, Redis 7) ↻ | 176 passed, 5 skipped, Redis tests active |
| Lint (ruff), types (mypy, 96 files), security (bandit) ↻ | clean |
| Web unit tests / production build / `npm audit` ↻ | 14 passed / 23 routes / no `next`-specific advisories |
| End-to-end flow (live API + dashboard, BFF auth/CSRF/proxy) ↻ | 23/23 checks passed |
| `make simulate` (single-episode quick check) ↻ | fixed: the CLI had no `episode` subcommand to run one |
| Demo scenarios (served models and rules-only) ↻ | 16 checks passed in each mode |
| Model evaluation on held-out simulated users ↻ | distraction ROC-AUC 0.877 (ECE 0.022), content macro-F1 0.948 |
| Android: Kotlin compile against the Android 34 SDK with `-Werror`; domain unit tests | 137 classes, 13 tests passed (not re-run in the ↻ pass: that container has no Kotlin compiler or Android SDK jar available, itself consistent with the limitation below) |
| Simulation grid: 10 controllers × 5 personas × 10 seeds × 21 days | full system 8.35 unwanted min/day vs 13.1–17.1 for baselines |

**Not executed here** (no Docker daemon, no Google/Maven access, no API credentials, no users): container image
builds, the CI pipeline, the Android APK build and instrumented tests, live LLM/vision provider calls, and any
evaluation with real people. See [docs/limitations.md](docs/limitations.md) for the complete list.

> All quantitative results in this repository come from a **synthetic simulator written by the authors**. They show
> how the decision logic behaves under stated assumptions and are not evidence of real-world efficacy.

## Documentation

[Architecture](docs/architecture.md) · [Agents](docs/agents.md) · [API](docs/api.md) · [Database](docs/database.md) ·
[Security](docs/security.md) · [Privacy](docs/privacy.md) · [ML](docs/ml.md) · [Evaluation](docs/evaluation.md) ·
[Android](docs/android.md) · [Setup](docs/setup.md) · [Deployment](docs/deployment.md) ·
[Configuration](docs/configuration.md) · [Troubleshooting](docs/troubleshooting.md) ·
[Limitations and stubs](docs/limitations.md) · [Roadmap](docs/roadmap.md) · [Research report](docs/research-report.md)

## License

MIT.
