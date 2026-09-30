# Deploying MindGuard for free

A working public deployment on free tiers: **Neon** (PostgreSQL with pgvector) + **Upstash** (Redis, optional) +
**Render** (API container) + **Vercel** (dashboard) + **GitHub Actions** (Android APK). Nothing here costs money;
all of it has free-tier limits worth knowing before you start.

> Honest caveat: none of these providers were reachable from the environment where this project was built, so the
> steps below are written from their documented behaviour and the project's verified local runs. Expect to fix one
> or two small things on the first deploy — the logs will tell you which.

## 0. What the free tiers actually give you

| Piece | Provider | Free limit that matters |
|---|---|---|
| PostgreSQL + pgvector | Neon | ~0.5 GB storage, scales to zero (first query after idle is slow) |
| Redis | Upstash | 10k commands/day — optional; without it the API uses in-process limits and cache |
| API container | Render free web service | 512 MB RAM, **spins down after ~15 min idle**, cold start ~30–60 s |
| Dashboard | Vercel Hobby | Generous for this app; personal, non-commercial use |
| APK build | GitHub Actions | 2,000 minutes/month on private repos, unlimited on public |

The API loads XGBoost, LightGBM and scikit-learn; measured process RSS is ~270 MB, which fits 512 MB but not
comfortably. If Render OOMs, set `MODEL_DIR=/srv/ml/empty` to run with the interpretable rules only (the app degrades
cleanly and says so at `/api/models/status`), or deploy on Fly.io with 512 MB–1 GB.

## 1. Database (Neon)

1. Create a project at neon.tech and copy the connection string; it looks like
   `postgresql://user:pass@ep-xxx.aws.neon.tech/neondb?sslmode=require`.
2. Enable pgvector once, from the Neon SQL editor: `CREATE EXTENSION IF NOT EXISTS vector;`
3. Apply the schema from your machine (Render's free tier has no shell):

```bash
cd backend
DATABASE_URL="postgresql://user:pass@ep-xxx.aws.neon.tech/neondb?sslmode=require" alembic upgrade head
```

You can paste the provider's URL as-is: MindGuard normalises `postgres://`/`postgresql://` to the asyncpg driver,
converts `sslmode=` to asyncpg's `ssl=`, and drops libpq-only parameters such as `channel_binding`.

## 2. Cache (Upstash, optional)

Create a Redis database, copy the `rediss://…` URL, and set it as `REDIS_URL`. Skip this and everything still works;
rate limits and the LLM cache simply become per-process.

## 3. API (Render)

1. Push the repository to GitHub (section 6 below), then in Render choose **New → Blueprint** and point it at the
   repo — `render.yaml` describes the service — or **New → Web Service** with runtime *Docker* and Dockerfile path
   `infra/docker/backend.Dockerfile`.
2. Set the environment variables: `ENVIRONMENT=production`, `JWT_SECRET` (generate 48 random characters),
   `DATABASE_URL` (Neon), `REDIS_URL` (Upstash, optional), `CORS_ORIGINS=https://<your-app>.vercel.app`,
   `ADMIN_EMAILS=you@example.com`, and `LLM_PROVIDER=mock` until you add a key.
3. Deploy, then check `https://<service>.onrender.com/ready` and `/docs`.

The backend refuses to start in production with a `JWT_SECRET` shorter than 32 characters — that is deliberate.

## 4. Dashboard (Vercel)

1. **Add New → Project**, import the repo, set **Root Directory** to `web`. The framework preset is Next.js.
2. Environment variable: `MINDGUARD_API_URL=https://<service>.onrender.com` (server-side only; not `NEXT_PUBLIC_`).
3. Deploy, open the URL, register an account. Register with the address in `ADMIN_EMAILS` to get the developer console.

Because the dashboard talks to the API from its own server, the browser never holds a token and you do not need to
open CORS for it.

## 5. Android

`.github/workflows/ci.yml` already builds a debug APK and uploads it as a build artifact. Push, open the Actions run,
download `mindguard-debug-apk`, and install it with `adb install`. Point the app at your API by changing
`LocalSettings.DEFAULT_API` before building, or by editing the base URL in the app's settings. Google Play distribution
needs a signed release build, a privacy policy URL and a declaration for the special-use foreground service — out of
scope for a free deployment.

## 6. First deploy checklist

- [ ] `CREATE EXTENSION vector` run on Neon, `alembic upgrade head` applied
- [ ] `JWT_SECRET` set to 32+ random characters (`openssl rand -base64 48`)
- [ ] `ADMIN_EMAILS` set **before** you register, otherwise your account is a normal user
- [ ] `MINDGUARD_API_URL` on Vercel points at the Render URL, with no trailing slash
- [ ] `/ready` returns 200 and `/api/models/status` shows `ml_available: true`
- [ ] Optional: `METRICS_TOKEN` so `/metrics` is not public
- [ ] Optional: `LLM_PROVIDER=anthropic` + `LLM_API_KEY`, and `MODEL_PRICING_JSON` so cost accounting is real

## 7. Things that will surprise you on free tiers

- **Cold starts.** Render free spins down; the Android app's first evaluation after idle may time out and fall back
  to an offline reminder. That is the designed behaviour, but it makes the app feel slow. A cheap paid instance or
  Fly.io's always-on allowance fixes it.
- **Neon scale-to-zero** adds a second or two to the first query.
- **Upstash command limits** are easy to hit if you set aggressive rate limits; the in-process fallback keeps working.
- **No background worker.** Everything runs in the API process; there is no queue to provision.
