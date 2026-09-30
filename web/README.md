# MindGuard Web Dashboard

A single-file dashboard (`index.html`) served directly by the FastAPI
backend at `http://localhost:8000/` — no Node toolchain, no build step.
It consumes the live API:

- `POST /api/events` — simulate a session event, see the authorized
  decision + user-visible explanation, then give feedback on it.
- `POST /api/interventions/feedback` — answer an intervention (accepted /
  ignored / overrode); this now drives the real bandit + memory update.
- `GET /api/analytics/daily` — intervention count, acceptance rate,
  override rate, decision breakdown.
- `GET /api/goals` / `POST /api/goals` — the "personal AI constitution":
  natural-language goal → structured policy.
- `GET /api/agent/runs` — full agent reasoning traces per event.
- `DELETE /api/user/data` — full data deletion for the selected user.

Run: `make run` (or `cd backend && py -3 -m uvicorn app.main:app --reload`),
then open http://localhost:8000/.

A Next.js/TypeScript rewrite for production multi-user deployment remains a
documented next step (see docs/architecture.md); the API contract is
identical.
