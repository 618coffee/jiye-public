#!/usr/bin/env bash
# Manually import notes from a JSONL file (one note per line).
# Each line must have at minimum: { "id", "title", "body" }.
set -euo pipefail
if [[ -z "${1:-}" ]]; then
  echo "Usage: $0 path/to/notes.jsonl"
  exit 1
fi
FILE="$(realpath "$1")"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
source .venv/bin/activate
python3 -c "
import json, sys
from app.main import get_store
from app.services.ingest import ingest_note
s = get_store(); s.init_schema()
n = 0
with open('$FILE','r',encoding='utf-8') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        ingest_note(s, json.loads(line)); n+=1
print(f'Ingested {n} notes from $FILE')
"
