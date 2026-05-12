"""Best-effort backfill for notes that only stored remote cover URLs.

Old lightweight imports often persisted ``cover`` but left ``image_paths`` and
``image_thumbnails`` empty. xhs CDN URLs can expire or reject hotlinking later,
so this script tries to cache any still-fetchable cover as a local thumbnail.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services import ocr as ocr_service  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


def load_candidates(limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT id, title, cover, image_paths, image_thumbnails
        FROM notes
        WHERE cover IS NOT NULL AND cover != ''
          AND (
            image_thumbnails IS NULL
            OR image_thumbnails = '[]'
            OR image_thumbnails LIKE '[null%'
          )
        ORDER BY saved_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def update_note(store: SqliteStore, row: sqlite3.Row, thumb_url: str) -> None:
    try:
        images = json.loads(row["image_paths"] or "[]")
    except Exception:
        images = []
    if row["cover"] not in images:
        images.insert(0, row["cover"])
    thumbs: list[str | None] = [None] * len(images)
    thumbs[0] = thumb_url
    with store._conn() as c:
        c.execute(
            """UPDATE notes
               SET image_paths = ?, image_thumbnails = ?
               WHERE id = ?""",
            (
                json.dumps(images, ensure_ascii=False),
                json.dumps(thumbs, ensure_ascii=False),
                row["id"],
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = load_candidates(args.limit)
    print(f"cover thumbnail candidates: {len(rows)}")
    if args.dry_run:
        for row in rows[:20]:
            print(f"- {row['id']} {row['title'][:60]}")
        return

    store = SqliteStore(settings.db_path)
    store.init_schema()
    ok = failed = 0
    for row in rows:
        result = ocr_service.process_images(row["id"], [row["cover"]], enabled=True)
        thumb_url = result[0].thumb_url if result else None
        if thumb_url:
            update_note(store, row, thumb_url)
            ok += 1
            print(f"cached {ok:>3}: {row['title'][:70]}", flush=True)
        else:
            failed += 1
            print(f"failed {row['id']}: {row['title'][:70]}", flush=True)
    print(f"DONE cached={ok} failed={failed}")


if __name__ == "__main__":
    main()
