# Database

PostgreSQL 16 with pgvector in production; SQLite (aiosqlite) for local development and the test suite. SQLAlchemy 2
async models, Alembic migrations, 26 tables, 30 indexes, 47 check constraints.

## Tables by area

| Area | Tables |
|---|---|
| Identity | `users`, `user_preferences`, `refresh_tokens`, `consents` |
| Usage | `events`, `sessions`, `apps`, `behavior_features` |
| Goals and rules | `goals`, `policies`, `policy_versions`, `policy_overrides` |
| Decisions | `risk_scores`, `interventions`, `intervention_outcomes`, `content_signals` |
| Agent traces | `agent_runs`, `agent_steps`, `tool_calls`, `llm_calls` |
| Learning | `memories`, `embeddings`, `bandit_states`, `model_versions` |
| Knowledge and audit | `knowledge_documents`, `audit_logs` |

## Constraints that matter

- **Event idempotency**: `(user_id, client_event_id)` is unique, so a phone that retries an upload after a timeout
  never double-counts a session. The Android tracker derives ids deterministically for the same reason.
- **Audit chain**: `audit_logs` rows carry a monotonic `seq` and a SHA-256 hash over the previous row, written under
  a PostgreSQL advisory lock. `GET /api/admin/audit/verify` recomputes the chain; tampering is detectable.
- **Vector search**: `embeddings.vector` is a 384-dimension `vector` column with an HNSW cosine index
  (`ix_embeddings_hnsw_384`). On SQLite the same code path falls back to a brute-force cosine scan.
- **Check constraints** enforce the shared enum vocabulary (decisions, outcomes, run statuses, tool statuses) at the
  database level, not only in Python, and bound probabilities to [0, 1].
- **Retention**: `user_preferences.retention_days` (default 90) governs history pruning; deletion is implemented in
  `app/services/privacy.py` for both "history" and "all" scopes.

## Migrations

```bash
cd backend
alembic upgrade head          # apply
alembic check                 # fails if the models drift from the migrations
alembic revision --autogenerate -m "describe change"
alembic downgrade base        # verified to run cleanly
```

Verified on PostgreSQL 16 + pgvector 0.6: upgrade → `alembic check` reports no drift → downgrade to base → upgrade
again. The API creates tables directly only for SQLite development and tests (`create_all`, which imports every model
first so the schema is never partially created).

## Local databases

```bash
# PostgreSQL with pgvector via docker compose
docker compose up postgres -d
export DATABASE_URL=postgresql+asyncpg://mindguard:mindguard@localhost:5432/mindguard

# SQLite needs nothing
export DATABASE_URL=sqlite+aiosqlite:///./mindguard.db
```

Run the test suite against PostgreSQL with `TEST_DATABASE_URL=postgresql+asyncpg://…` and against Redis with
`TEST_REDIS_URL=redis://…`; without them the suite uses SQLite and skips the Redis tests.
