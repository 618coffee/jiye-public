"""Re-fetch already-ingested notes whose local image cache is missing."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.inbox_promote import promote_notes  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


def load_note_ids(limit: int | None = None) -> list[str]:
    sql = """
        SELECT n.id
        FROM notes n
        JOIN note_inbox i ON i.note_id = n.id
        WHERE n.cover IS NOT NULL AND n.cover != ''
          AND i.raw_meta LIKE '%"xsec_token"%'
          AND (
            n.image_thumbnails IS NULL
            OR n.image_thumbnails = '[]'
            OR n.image_thumbnails LIKE '[null%'
          )
        ORDER BY n.saved_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = sqlite3.connect(settings.db_path)
    try:
        return [r[0] for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-spa-fallback", action="store_true")
    args = parser.parse_args()

    note_ids = load_note_ids(args.limit)
    print(f"repromote candidates: {len(note_ids)}")
    if args.dry_run:
        for note_id in note_ids[:50]:
            print(f"- {note_id}")
        return

    store = SqliteStore(settings.db_path)
    store.init_schema()
    total_promoted = total_failed = total_skipped = 0
    for start in range(0, len(note_ids), args.batch_size):
        batch = note_ids[start:start + args.batch_size]
        res = promote_notes(
            store,
            batch,
            max_per_request=len(batch),
            allow_spa_fallback=args.allow_spa_fallback,
        )
        total_promoted += res.promoted
        total_failed += res.failed
        total_skipped += res.skipped
        print(
            f"batch {start // args.batch_size + 1}: "
            f"promoted={res.promoted} failed={res.failed} skipped={res.skipped}",
            flush=True,
        )
        for item in res.results:
            if item.status != "promoted":
                print(f"  {item.note_id}: {item.status} {item.error or ''}", flush=True)
    print(
        f"DONE promoted={total_promoted} failed={total_failed} skipped={total_skipped}"
    )


if __name__ == "__main__":
    main()
