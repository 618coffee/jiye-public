#!/usr/bin/env bash
# Re-seed the local DB (drops index.db and re-ingests samples).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -f "$ROOT/data/index.db"
cd "$ROOT/backend"
source .venv/bin/activate
python3 -c "
from app.main import get_store
from app.seed import seed
s = get_store(); s.init_schema()
n = seed(s)
print(f'Seeded {n} notes')
"
