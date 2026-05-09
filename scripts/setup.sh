#!/usr/bin/env bash
# One-time setup: install backend & frontend deps.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Backend Python venv + deps"
cd "$ROOT/backend"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -q -r requirements.txt
deactivate

echo "==> Frontend npm deps"
cd "$ROOT/frontend"
npm install --no-fund --no-audit

echo
echo "Setup complete. Next:"
echo "  ./scripts/dev.sh"
