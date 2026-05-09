#!/usr/bin/env bash
# Boot backend + frontend with one command.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  echo
  echo "==> Stopping…"
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting backend (uvicorn :8000)"
(
  cd "$ROOT/backend"
  source .venv/bin/activate
  uvicorn app.main:app --reload --port 8000 --log-level info
) &

echo "==> Starting frontend (next :3000)"
(
  cd "$ROOT/frontend"
  npm run dev
) &

echo
echo "Backend:  http://localhost:8000/api/health"
echo "Frontend: http://localhost:3000"
echo
wait
