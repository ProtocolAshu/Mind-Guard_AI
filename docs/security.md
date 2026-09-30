# Security

## Threat model

| Adversary | Capability | Main controls |
|---|---|---|
| Malicious social-media content | Text the user did not write reaches the classifier and possibly an LLM | Sanitisation, `<untrusted_*>` wrapping, injection detection with quarantine (detected content is never sent to a model), read-only tool surface, deterministic authorisation of every proposal |
| Compromised or manipulated LLM | Returns a decision that disables protection, changes policy, or invents tools | Strict output schemas, eligible-set validation, guardrail authorisation, no write or device tools for the model, `LLM_FALLBACK` degradation |
| Another user of the same deployment | Guesses ids, replays tokens, reads others' data | Every query is user-scoped, 404 (not 403) for unknown ids, rotating refresh tokens with family revocation, per-user rate limits |
| Stolen device | Reads tokens or replays commands | Tokens encrypted with a non-exportable Android Keystore key, commands carry an HMAC ticket bound to user/run/app with an expiry, device-side validation of every command |
| Network attacker | Intercepts or replays traffic | HTTPS enforced by the client and the network security config, no cleartext except the emulator host in debug, no secrets in logs |
| Curious operator | Reads traces or logs | Raw content is never stored, traces are summarised with secrets redacted, audit log is hash-chained, `/metrics` can require a bearer token |

## Authentication and sessions

- Argon2id password hashing with a constant-time dummy verification for unknown accounts.
- JWT access tokens (15 minutes by default) with issuer/audience validation and an injectable clock.
- Refresh tokens are opaque, hashed at rest, rotated on every use, and **reuse revokes the entire family** —
  verified end-to-end through the dashboard's proxy.
- The web dashboard never sees a token: a Next.js backend-for-frontend keeps both tokens in httpOnly, `SameSite=Strict`
  cookies (the refresh cookie scoped to `/api`), checks `Sec-Fetch-Site`/`Origin` on every state-changing request,
  validates and re-encodes the upstream path, and forwards only allow-listed headers.

## Application controls

- Uniform error envelope; no stack traces or SQL in responses.
- Request body size limit, per-route rate limits (auth, LLM, default) backed by Redis when configured with an
  in-process fallback that still enforces limits when Redis is down.
- Security headers on API and dashboard responses, including a content security policy with `frame-ancestors 'none'`.
- Parameterised queries throughout (SQLAlchemy); injection payloads are stored and returned inert.
- Device commands carry short expiries, duration caps and reversible actions; the Android client re-validates
  expiry, duration, essential-app status and reversibility before acting.

## Test coverage (section 36)

`backend/tests/security/test_security_cases.py` covers all ten required cases and runs in CI:

1. prompt injection through social-media text · 2. malicious tool arguments rejected by schemas ·
3. unauthorised intervention creation · 4. token leakage · 5. user data access violation · 6. cross-user data
leakage · 7. SQL injection payloads are inert · 8. API abuse (rate and payload limits) · 9. replay attacks ·
10. invalid policy injection.

The dashboard's proxy adds 23 end-to-end checks (`tests/e2e/bff_flow_check.py`), including CSRF rejection, path
traversal blocking, refresh rotation, family revocation on reuse, and logout invalidation.

Static analysis: `bandit` (clean, no findings), `ruff` security rules (`S`), `mypy` strict on the app package,
`pip-audit` and `npm audit` in CI.

## Known gaps

- No penetration test, no fuzzing campaign and no load test have been run.
- Secrets management assumes environment variables; no KMS or vault integration.
- Multi-tenant deployments would need row-level security in addition to the application-level scoping used here.
- Certificate pinning is not implemented on Android; the client enforces HTTPS but trusts the system store.
