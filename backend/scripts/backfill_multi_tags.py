"""One-shot: recompute multi-label tags for every pending/promoted inbox row.

Run AFTER deploying the multi-tag schema if you have an existing inbox
that was tagged with single cluster_ids. Idempotent — re-running just
recomputes against the current centroids.

Usage (from backend/):
    python -m scripts.backfill_multi_tags
"""
from __future__ import annotations

from app.config import settings
from app.providers import get_embedding_provider
from app.services.inbox_clustering import (
    MAX_TAGS_PER_NOTE,
    PRIMARY_MIN_SIMILARITY,
    SECONDARY_MAX_DELTA_FROM_TOP,
    SECONDARY_MIN_SIMILARITY,
    UNCLUSTERED_ID,
    _cosine,
    build_existing_centroids,
)
from app.store.sqlite_store import SqliteStore


def main() -> None:
    store = SqliteStore(settings.db_path)
    store.init_schema()

    rows = store.list_inbox(status=["pending", "promoted"], limit=100_000)
    rows = [r for r in rows if (r.get("title") or "").strip()]
    if not rows:
        print("no rows to backfill")
        return

    embed = get_embedding_provider()
    cluster_ids, centroids, _existing = build_existing_centroids(store, embed)
    if not centroids:
        print("no existing clusters; run /api/inbox/cluster first")
        return

    titles = [r["title"] for r in rows]
    chunk = 256
    vecs: list[list[float]] = []
    for i in range(0, len(titles), chunk):
        vecs.extend(embed.embed(titles[i : i + chunk]))

    processed = 0
    multi_tagged = 0
    unclustered = 0
    for row, vec in zip(rows, vecs):
        nid = row["note_id"]
        if not vec:
            store.set_note_tags(nid, [(UNCLUSTERED_ID, 0.0)])
            unclustered += 1
            continue
        scored = [
            (cluster_ids[i], _cosine(vec, c)) for i, c in enumerate(centroids)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        if not scored or scored[0][1] < PRIMARY_MIN_SIMILARITY:
            store.set_note_tags(nid, [(UNCLUSTERED_ID, scored[0][1] if scored else 0.0)])
            unclustered += 1
            processed += 1
            continue
        tags: list[tuple[str, float]] = [scored[0]]
        top_score = scored[0][1]
        for cid, sc in scored[1:]:
            if len(tags) >= MAX_TAGS_PER_NOTE:
                break
            if sc < SECONDARY_MIN_SIMILARITY:
                break
            if (top_score - sc) > SECONDARY_MAX_DELTA_FROM_TOP:
                break
            tags.append((cid, sc))
        store.set_note_tags(nid, tags)
        if len(tags) >= 2:
            multi_tagged += 1
        processed += 1

    print(
        f"processed={processed}, multi_tagged={multi_tagged}, unclustered={unclustered}"
    )


if __name__ == "__main__":
    main()
