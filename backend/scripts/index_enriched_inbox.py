"""Index already-enriched inbox notes into the main searchable KB.

This script is local-only: it reads ``note_inbox.raw_meta.detail`` rows that
already contain body/tags, then feeds them through ``ingest_note`` so the UI
search endpoint can retrieve them by title, body, tags, and inferred places.

Usage:
    cd backend && python scripts/index_enriched_inbox.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.ingest import ingest_note  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


def _detail(meta: dict[str, Any]) -> dict[str, Any]:
    detail = meta.get("detail") or {}
    return detail if isinstance(detail, dict) else {}


def load_rows(limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT note_id, url, title, cover_url, author, saved_at,
               discovered_at, status, raw_meta
        FROM note_inbox
        WHERE raw_meta LIKE '%"detail"%'
          AND raw_meta LIKE '%"ok": true%'
        ORDER BY discovered_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def build_raw(row: sqlite3.Row) -> dict[str, Any] | None:
    meta = json.loads(row["raw_meta"] or "{}")
    detail = _detail(meta)
    body = (detail.get("body") or "").strip()
    tags = [str(t).strip() for t in (detail.get("tags") or []) if str(t).strip()]
    if not body and not tags:
        return None
    return {
        "id": row["note_id"],
        "url": row["url"],
        "title": (row["title"] or "").strip(),
        "body": body,
        "author": detail.get("author") or row["author"],
        "cover": row["cover_url"],
        "images": [],
        "saved_at": row["saved_at"] or row["discovered_at"],
        "published_at": detail.get("published_at"),
        "source": "inbox_detail_index",
        "tags": tags,
    }


def mark_promoted(note_ids: list[str]) -> None:
    if not note_ids:
        return
    conn = sqlite3.connect(settings.db_path)
    try:
        placeholders = ",".join("?" for _ in note_ids)
        conn.execute(
            f"UPDATE note_inbox SET status='promoted', last_error=NULL WHERE note_id IN ({placeholders})",
            note_ids,
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-inbox-status",
        action="store_true",
        help="Do not mark successfully indexed inbox rows as promoted.",
    )
    args = parser.parse_args()

    rows = load_rows(args.limit)
    print(f"enriched inbox rows: {len(rows)}")
    if args.dry_run:
        for row in rows[:10]:
            raw = build_raw(row)
            tags = raw.get("tags", []) if raw else []
            body_len = len(raw.get("body", "")) if raw else 0
            print(f"- {row['note_id']} body={body_len} tags={len(tags)} title={row['title']}")
        return

    store = SqliteStore(settings.db_path)
    store.init_schema()

    indexed: list[str] = []
    skipped = 0
    failed = 0
    for row in rows:
        raw = build_raw(row)
        if not raw:
            skipped += 1
            continue
        try:
            ingest_note(store, raw)
            indexed.append(row["note_id"])
            print(f"indexed {len(indexed):>3}: {raw['title'][:70]}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"failed {row['note_id']}: {type(exc).__name__}: {exc}", flush=True)

    if indexed and not args.keep_inbox_status:
        mark_promoted(indexed)

    print(
        f"DONE indexed={len(indexed)} skipped={skipped} failed={failed} "
        f"marked_promoted={0 if args.keep_inbox_status else len(indexed)}"
    )


if __name__ == "__main__":
    main()