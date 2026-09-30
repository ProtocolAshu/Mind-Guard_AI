# Deployment

## Docker compose (development and small deployments)

```bash
cp .env.example .env    # set JWT_SECRET (32+ random characters) at minimum
docker compose up --build
```

Services: `postgres` (pgvector/pgvector:pg16), `redis`, `migrate` (runs `alembic upgrade head` once and exits),
`backend` (starts only after migrations succeed) and `web`. Both images run as non-root with health checks; the API
image installs `libgomp1` for LightGBM/XGBoost and carries the verified model registry at `/srv/ml/models`.

**These images have never been built or run in this environment** — no Docker daemon was available. Treat the
Dockerfiles and compose file as reviewed-but-unexecuted, and build them once before relying on them.

## Production checklist

1. `ENVIRONMENT=production` and a `JWT_SECRET` of at least 32 random characters — the backend refuses to start otherwise.
2. PostgreSQL with the `vector` extension; run `alembic upgrade head` as a release step, never `create_all`.
3. `REDIS_URL` set, so rate limits and the LLM cache are shared across workers.
4. `CORS_ORIGINS` restricted to the dashboard's origin; terminate TLS in front of the API and the dashboard.
5. `ADMIN_EMAILS` limited to real operators; `METRICS_TOKEN` set so `/metrics` is not public.
6. Provider keys (`LLM_API_KEY`, `VISION_API_KEY`, `EMBEDDING_API_KEY`) and `MODEL_PRICING_JSON` for cost accounting;
   set the token and USD budgets deliberately.
7. `OTEL_ENDPOINT` for traces; scrape `/metrics` for request, decision, tool and LLM counters.
8. Back up PostgreSQL. Everything else (Redis, model files) is reproducible.

## Scaling and operations

- The API is stateless: scale horizontally behind a load balancer. Redis makes rate limits and the response cache
  consistent between instances.
- Model artifacts are read-only and SHA-256 verified at load; ship them with the image or a mounted volume, and
  watch `GET /api/models/status` for load errors.
- `GET /ready` checks the database (and reports degraded dependencies); `GET /health` is a liveness probe.
- The audit chain is verifiable at any time via `GET /api/admin/audit/verify`; alert on a failure.
- Cost: `GET /api/admin/stats` reports month-to-date spend, a projected monthly figure and budget usage.

## CI

`.github/workflows/ci.yml` runs six jobs: backend (ruff, mypy, bandit, pip-audit, SQLite tests, PostgreSQL+Redis
tests, migration drift check, generated-docs check), demo scenarios, web (typecheck, unit tests, build, npm audit),
end-to-end BFF flow, Android (`:domain:test` and `:app:assembleDebug`, uploading the APK), and container image
builds. The workflow has never been executed — there is no CI runner in this environment.
