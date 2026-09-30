# Getting started from a fresh download

You have a plain source tree (no `.git` history yet). This is the path from that zip to a running
local instance, a GitHub repo with CI, and a free public deployment. For depth on any one step, see
[docs/setup.md](docs/setup.md) (local development) or [docs/deploy-free.md](docs/deploy-free.md)
(hosting). This file is the connective tissue between the two.

## 1. Open it in VS Code

```bash
unzip mindguard.zip && cd mindguard
code .
```

Accept the "Install recommended extensions?" prompt (Python, Pylance, Ruff, Mypy, ESLint, Prettier,
Tailwind CSS, Docker, Kotlin, YAML, GitHub Actions — listed in `.vscode/extensions.json`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install && make install-web
cp .env.example .env                                     # defaults: SQLite + mock LLM, fully offline
echo "MINDGUARD_API_URL=http://localhost:8000" > web/.env.local
```

VS Code picks up `.venv` automatically (pinned in `.vscode/settings.json`). Press **F5**, choose
**"API + Dashboard"**, and both the API (with reload) and the dashboard start under the debugger —
`http://localhost:8000/docs` and `http://localhost:3000`. Individual launch configs and `make`-backed
tasks (`install: backend`, `test: all`, `demo`, `docker: up`, …) are in `.vscode/launch.json` and
`.vscode/tasks.json` if you want to run pieces separately.

Sanity check: `make test-all` — lint, types, security scan, backend and web tests, all offline.

## 2. Push it to GitHub

```bash
git init -b main
git add -A
git commit -m "Initial commit"
```

Create an empty repository on GitHub (skip the README/.gitignore/license options — you already have
all three), then:

```bash
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Nothing further to configure: `.github/workflows/ci.yml` runs on push with zero secrets required — it
brings up its own PostgreSQL + pgvector and Redis service containers, so backend, demo, web, end-to-end,
Android (unit tests + a downloadable debug APK), and both Docker images are all verified on every push,
on the free tier of a public or private repo alike.

## 3. Deploy it (free tier throughout)

Full detail, first-deploy checklist and troubleshooting: [docs/deploy-free.md](docs/deploy-free.md).
Short version:

1. **Neon** — new Postgres project → `CREATE EXTENSION IF NOT EXISTS vector;` → run
   `alembic upgrade head` against it from your machine.
2. **Upstash** (optional) — a Redis database for rate limiting and the LLM cache; the app runs fine
   without it.
3. **Render** — New → Blueprint, point at your GitHub repo (`render.yaml` is already checked in).
   Set `JWT_SECRET`, `DATABASE_URL`, `CORS_ORIGINS`, `ADMIN_EMAILS`, `LLM_PROVIDER=mock` (or
   `anthropic` + `LLM_API_KEY` for real reasoning).
4. **Vercel** — Add New Project on the same repo, root directory `web`, env var
   `MINDGUARD_API_URL=<your Render URL>`.
5. Register in the dashboard using the address you put in `ADMIN_EMAILS` first, to get the admin
   console on your own account.

The free Render tier spins down after ~15 minutes idle, so the first request after a lull is slow
(cold start) — a tier tradeoff, not a bug.
