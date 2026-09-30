# Troubleshooting

**The backend will not start: "JWT_SECRET must be set to at least 32 characters in production".**
Set a real secret (`openssl rand -base64 48`). In development the secret may be empty and an ephemeral one is
generated per process, which invalidates tokens on restart.

**`GET /api/models/status` reports `ml_available: false` or load errors.**
The registry files are missing or their SHA-256 no longer matches, or `xgboost`/`lightgbm` are not installed
(`libgomp1` is required inside containers). Scoring still works — it falls back to the interpretable rules — but it
is weaker. Run `make train` or restore `ml/models/`.

**Decisions never use the LLM.** By design: it is consulted only when distraction risk is inside
`LLM_AMBIGUITY_LOW`–`LLM_AMBIGUITY_HIGH` (0.45–0.72), more than one action is eligible, and the user granted
`cloud_ai_reasoning`. With the trained models loaded, confident cases resolve deterministically. Check
`GET /api/agent/runs/{id}` to see which branch a run took.

**Everything is `ALLOW` and no interventions appear.** In order of likelihood: the guardian is off, an override
(pause, emergency, allow-once) is active, `usage_monitoring` consent is missing, the app is on the essential list,
no rule matches and risk is low, or a guardrail suppressed a duplicate. The intervention's `guardrail_flags` and
`reason_codes` name the cause; the dashboard shows both in plain language.

**The dashboard shows "MindGuard API is unreachable".** `MINDGUARD_API_URL` is wrong, or the API is not running.
It is a server-side variable for the Next.js process, not a `NEXT_PUBLIC_` one.

**Dashboard requests return 403 `csrf_rejected`.** The request reached the proxy without a same-origin
`Sec-Fetch-Site` or `Origin` header. Call the dashboard through its own origin rather than a rewriting proxy.

**Sign-in works, then requests 401 after a while.** The refresh token was rotated in another tab or replayed;
reuse revokes the whole family by design. Sign in again.

**Android: no sessions appear.** Usage access is the special permission that must be granted in system settings; the
Permissions screen shows what the phone last reported. Also check that the app is in the monitored list (essential
apps are never monitored) and that battery optimisation has not killed the foreground service.

**Android: a restriction shows as a notification instead of a pause screen.** "Display over other apps" is not
granted, so the capability guardrail downgraded the action. This is expected and flagged as `CAPABILITY_UNAVAILABLE`.

**Tests fail with "no such table".** Run them through `pytest` from `backend/` so the fixtures create the schema;
`create_all` imports every model first. For PostgreSQL runs, apply migrations and set `TEST_DATABASE_URL`.

**Experiments stop before finishing.** `research/run_experiments.py` is resumable and respects `--budget-seconds`:
rerun it until it reports `remaining chunks: 0`, then run it with `--analyse`.
