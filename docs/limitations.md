# Limitations, stubs and what must be connected

Per section 51, nothing here pretends to be finished when it is not.

## Fixed during independent re-verification

A later pass installed everything from scratch in a fresh container (including PostgreSQL 16 + pgvector and Redis,
which were not preinstalled there) and re-ran the suites rather than trusting these numbers. Two real defects
surfaced and were fixed:

- **Rate-limit test isolation.** `test_8_api_abuse_rate_limits_and_payload_limits` failed against a real Redis
  because the test transport has no socket, so every test in the session shares one constant client-IP identity —
  and the Redis-backed limiter is a fixed-window counter keyed by that identity, shared globally for the window
  regardless of which test hit it first. Fixed by flushing Redis at the start of that test. This was a test-harness
  gap, not a production bug: real clients have distinct IPs.
- **Vulnerable pinned dependency.** `web/package.json` pinned `next@15.5.4`, which has a disclosed unauthenticated
  RCE and several other advisories. Patched to `15.5.25` (same major version, no breaking changes); confirmed
  `npm audit`, the unit tests and a production build are all still clean. One transitive `postcss` advisory remains,
  fixable only by a `next@16` major upgrade, which was not attempted blindly without time to verify the dashboard
  against it.
- **`make simulate` didn't do what its own description promised.** `simulator/__main__.py` only ever wired up the
  dataset generator (`generate`); there was no way to run "a single simulator episode" as the Makefile's comment and
  section 29 describe, even though `simulator/runner.run_episode` already existed and is exactly what the experiment
  grid uses internally. Added an `episode` subcommand (persona, controller, seed, days, optional served risk model)
  and pointed the Makefile target at it.
- **`make train`'s first step was a silent no-op.** It ran `python -m simulator.dataset`, but that module has no
  `if __name__ == "__main__":` guard — the command exits 0 immediately having generated nothing, so training
  proceeded on whatever dataset already happened to be on disk rather than a fresh one. Masked in this repo because
  `ml/datasets/synthetic/` was already committed. Fixed by pointing the target at `simulator generate` (the CLI
  fixed above). Verified end to end afterward: regeneration reproduces the committed dataset byte-for-byte, and
  retraining on it reproduces the committed metrics exactly (distraction ROC-AUC 0.877, content macro-F1 0.948).

## Implemented but never exercised against the real thing

| Component | State | To make it real |
|---|---|---|
| Anthropic LLM provider | Fully implemented against the Messages API; exercised only through the deterministic mock | Set `LLM_PROVIDER=anthropic` and `LLM_API_KEY`; verify cost accounting with `MODEL_PRICING_JSON` |
| OpenAI-compatible LLM provider | Implemented; never called against a live endpoint | Set `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL`, `LLM_API_KEY` |
| Vision provider | Implemented; never called against a live endpoint | `VISION_PROVIDER=anthropic` plus the screenshot consent scope |
| Remote embeddings | Implemented; the committed index uses the offline hashing embedder (384-d) | Set `EMBEDDING_PROVIDER=openai_compatible` and **re-embed**: dimensions must match the column and index |
| OpenTelemetry export | Wired; no collector was ever attached | Set `OTEL_ENDPOINT` and confirm spans arrive |
| Container images, compose stack | Written and reviewed; never built (no Docker daemon here) | `docker compose up --build` once and fix whatever surfaces |
| GitHub Actions CI | Written; never executed (no runner) | Push to a repository and iterate on the first run |
| Android APK | Kotlin type-checked against the real SDK and coroutines, domain tests run on the JVM; Gradle never ran (Google/Maven blocked) | `./gradlew :app:assembleDebug`, then install and test on a device |

## Deliberately not implemented

- **Push delivery to the device.** The phone polls and asks for an evaluation; there is no FCM dependency. A
  production deployment that wants server-initiated interventions must add it.
- **On-device ML.** Content classification happens server-side. The Android `ai` module defines the share-sheet path
  only; there is no TFLite model, and no code pretends there is.
- **Accessibility-service monitoring.** Deliberately absent: it would allow reading other apps' screen content, which
  the privacy posture and store policies rule out. Usage statistics are used instead.
- **Force-closing apps.** Not possible for a normal Android app; the overlay or a return to the home screen is used.
- **Multi-device coordination.** Sessions from several devices for one account are ingested, but there is no conflict
  resolution or per-device policy.

## Evidence limitations

- Every number in this repository comes from a **synthetic simulator written by the authors**. The persona model is
  an assumption, not observed behaviour. No real user has used this system.
- The doomscroll and continuation models are weak (ROC-AUC 0.634 and 0.605); only the distraction model is strong.
- The contextual bandit did not improve outcomes in the simulation and was slightly worse than the deterministic
  ladder; memory and personalisation showed no measurable effect at a 21-day horizon.
- Battery, network and CPU impact on a real phone are **unmeasured placeholders**.
- No security penetration test, load test or fuzzing campaign has been run.
- The "satisfaction" and "unwanted minutes" metrics are simulator constructs, not validated psychometric measures.

## Where the code says so

`GET /api/models/status` reports model availability and load errors; `GET /api/agent/status` reports whether an LLM
provider is configured; the dashboard shows "AI reasoning was unavailable; rules decided" whenever a run degraded;
`research/results/agent_eval.json` carries an explicit `[PLACEHOLDER]` for battery impact.
