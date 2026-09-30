#!/usr/bin/env bash
# Starts the FastAPI backend (SQLite, mock LLM) and the Next.js production server, runs the BFF flow check, stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
DB="$(mktemp -d)/mindguard-e2e.db"
( cd "$ROOT/backend" && ENVIRONMENT=development JWT_SECRET="e2e-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  DATABASE_URL="sqlite+aiosqlite:///$DB" LLM_PROVIDER=mock LOG_JSON=false RATE_LIMIT_AUTH_PER_MINUTE=100 \
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning ) &
API_PID=$!
( cd "$ROOT/web" && NEXT_TELEMETRY_DISABLED=1 MINDGUARD_API_URL=http://127.0.0.1:8000 exec npx next start -p 3000 -H 127.0.0.1 ) &
WEB_PID=$!
cleanup() { kill "$API_PID" "$WEB_PID" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 && curl -fsS http://127.0.0.1:3000/login >/dev/null 2>&1; then break; fi
  sleep 1
done
"$PY" "$ROOT/tests/e2e/bff_flow_check.py"
