# Setup and local development

Requirements: Python 3.12, Node 22, and optionally PostgreSQL 16 with pgvector plus Redis 7 (Docker provides both).
Java 17 and the Android SDK are needed only to build the app.

## Backend

```bash
python3.12 -m venv .venv && source .venv/bin/activate
make install                                  # backend/requirements-dev.txt
cp .env.example .env                          # set JWT_SECRET for anything non-local
cd backend && alembic upgrade head            # PostgreSQL; SQLite can auto-create instead
make api                                      # http://localhost:8000/docs
```

With no `DATABASE_URL`, the backend uses `sqlite+aiosqlite:///./mindguard.db`, which is fine for development.
The default `LLM_PROVIDER=mock` keeps everything offline and deterministic.

## Dashboard

```bash
make install-web
echo "MINDGUARD_API_URL=http://localhost:8000" > web/.env.local
make web            # http://localhost:3000
```

Register an account in the dashboard, then walk through onboarding. To see the developer console, add your address
to `ADMIN_EMAILS` **before** registering.

## Models, data and demos

```bash
make train      # regenerate the synthetic dataset and retrain (models are committed, so this is optional)
make evaluate   # metrics and figures
make demo       # six agent scenarios, narrated and checked
make seed       # synthetic users for the admin console
```

## LLM, vision and embedding providers

| Variable | Values | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock`, `anthropic`, `openai_compatible`, `disabled` | `mock` is deterministic and offline; `disabled` forces rules-only |
| `LLM_API_KEY`, `LLM_BASE_URL` | — | `LLM_BASE_URL` points the OpenAI-compatible client at any gateway |
| `LLM_REASONING_MODEL`, `LLM_FAST_MODEL`, `LLM_FALLBACK_MODEL` | model ids | Cheap model for classification, stronger model for ambiguous decisions |
| `MODEL_PRICING_JSON` | `{"model":{"input_per_mtok":3,"output_per_mtok":15}}` | Enables cost accounting and the monthly projection |
| `VISION_PROVIDER` | `disabled`, `anthropic` | Screenshot analysis; requires the screenshot consent scope |
| `EMBEDDING_PROVIDER` | `hashing`, `openai_compatible` | `hashing` is offline, 384-dimension, and what the committed index uses |

Budgets and safety rails: `LLM_USER_DAILY_TOKEN_BUDGET`, `LLM_MONTHLY_BUDGET_USD`, `LLM_TIMEOUT_SECONDS`,
`LLM_MAX_RETRIES`, `LLM_CACHE_TTL_SECONDS`, `LLM_AMBIGUITY_LOW/HIGH`. Full list in [configuration.md](configuration.md).

## Android

```bash
cd android && gradle wrapper --gradle-version 8.10.2 && ./gradlew :app:assembleDebug
```

Point the app at a development backend by editing `LocalSettings.DEFAULT_API` or the base URL in settings; the
emulator reaches the host at `http://10.0.2.2:8000`, which is the only cleartext exception in the network config.

## Testing

```bash
make test                                   # backend (SQLite) + web unit tests
make test-postgres TEST_DATABASE_URL=postgresql+asyncpg://mindguard:mindguard@localhost:5432/mindguard_test \
                   TEST_REDIS_URL=redis://localhost:6379/1
make lint typecheck security                # ruff, mypy + tsc, bandit
make e2e                                    # starts API + dashboard, runs the BFF flow check
make android-verify ANDROID_JAR=… COROUTINES_JAR=… KOTLINC=…   # offline Kotlin check + domain tests
```

## Regenerating documentation

`docs/api.md`, `docs/api/openapi.json` and `docs/configuration.md` are generated: run `make docs` after changing
routes or settings. CI fails if they are out of date.
