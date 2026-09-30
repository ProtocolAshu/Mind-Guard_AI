# Configuration

Generated from `backend/app/core/config.py` by `scripts/generate_docs.py`; do not edit by hand.
Every setting is read from an environment variable of the same name in upper case (or from `.env`).
Secrets have no usable default; in `production` the backend refuses to start with a weak `JWT_SECRET`.

| Variable | Type | Default |
|---|---|---|
| `ENVIRONMENT` | Literal['development', 'test', 'production'] | `'development'` |
| `APP_NAME` | str | `'MindGuard'` |
| `API_VERSION` | str | `'1.0.0'` |
| `LOG_LEVEL` | str | `'INFO'` |
| `LOG_JSON` | bool | `True` |
| `DATABASE_URL` | str | `'sqlite+aiosqlite:///./mindguard.db'` |
| `DATABASE_ECHO` | bool | `False` |
| `REDIS_URL` | str \| None | — |
| `JWT_SECRET` | pydantic.types.SecretStr | *(secret, required when used)* |
| `JWT_ISSUER` | str | `'mindguard'` |
| `JWT_AUDIENCE` | str | `'mindguard-clients'` |
| `ACCESS_TOKEN_TTL_MINUTES` | int | `15` |
| `REFRESH_TOKEN_TTL_DAYS` | int | `14` |
| `ALLOW_REGISTRATION` | bool | `True` |
| `ADMIN_EMAILS` | str | — |
| `CORS_ORIGINS` | str | `'http://localhost:3000'` |
| `MAX_REQUEST_BYTES` | int | `2000000` |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | int | `240` |
| `RATE_LIMIT_AUTH_PER_MINUTE` | int | `10` |
| `RATE_LIMIT_LLM_PER_MINUTE` | int | `20` |
| `EVENT_MAX_CLOCK_SKEW_SECONDS` | int | `900` |
| `EVENT_MAX_AGE_DAYS` | int | `7` |
| `EVALUATION_MIN_INTERVAL_SECONDS` | int | `45` |
| `METRICS_TOKEN` | pydantic.types.SecretStr | *(secret, required when used)* |
| `LLM_PROVIDER` | Literal['mock', 'anthropic', 'openai_compatible', 'disabled'] | `'mock'` |
| `LLM_API_KEY` | pydantic.types.SecretStr | *(secret, required when used)* |
| `LLM_BASE_URL` | str \| None | — |
| `LLM_REASONING_MODEL` | str | `'claude-sonnet-5'` |
| `LLM_FAST_MODEL` | str | `'claude-haiku-4-5-20251001'` |
| `LLM_FALLBACK_MODEL` | str \| None | — |
| `LLM_TIMEOUT_SECONDS` | float | `12.0` |
| `LLM_MAX_RETRIES` | int | `2` |
| `LLM_USER_DAILY_TOKEN_BUDGET` | int | `40000` |
| `LLM_MONTHLY_BUDGET_USD` | float | `25.0` |
| `LLM_CACHE_TTL_SECONDS` | int | `3600` |
| `MODEL_PRICING_JSON` | str | `'{}'` |
| `VISION_PROVIDER` | Literal['mock', 'anthropic', 'openai_compatible', 'disabled'] | `'disabled'` |
| `VISION_API_KEY` | pydantic.types.SecretStr | *(secret, required when used)* |
| `VISION_BASE_URL` | str \| None | — |
| `VISION_MODEL` | str | `'claude-sonnet-5'` |
| `EMBEDDING_PROVIDER` | Literal['hashing', 'openai_compatible'] | `'hashing'` |
| `EMBEDDING_API_KEY` | pydantic.types.SecretStr | *(secret, required when used)* |
| `EMBEDDING_BASE_URL` | str \| None | — |
| `EMBEDDING_MODEL` | str | `'text-embedding-3-small'` |
| `EMBEDDING_DIM` | int | `384` |
| `OTEL_ENDPOINT` | str \| None | — |
| `OTEL_SERVICE_NAME` | str | `'mindguard-backend'` |
| `MODEL_DIR` | pathlib.Path | `ml/models` |
| `KNOWLEDGE_DIR` | pathlib.Path | `backend/app/rag/corpus` |
| `RISK_ML_WEIGHT` | float | `0.6` |
| `BANDIT_ALGORITHM` | Literal['linucb', 'epsilon_greedy', 'thompson'] | `'linucb'` |
| `MIN_CONFIDENCE_HARD_ACTION` | float | `0.6` |
| `LLM_AMBIGUITY_LOW` | float | `0.45` |
| `LLM_AMBIGUITY_HIGH` | float | `0.72` |
| `DEFAULT_RETENTION_DAYS` | int | `90` |
