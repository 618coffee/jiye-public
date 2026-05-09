"""Hybrid search: FTS5 BM25 + vector cosine, weighted-merge."""
from __future__ import annotations

from typing import Any

from ..providers import get_embedding_provider, get_llm_provider
from ..store.sqlite_store import SqliteStore


# Relevance thresholds.
#
# Vector embeddings (especially multilingual e5) have a high baseline cosine
# similarity (~0.78-0.84 for *unrelated* CN/EN texts), so an absolute floor
# does not transfer between queries. The store's vector_search already
# applies a per-query z-score filter (mean + 1.5σ) so what we get here is
# already noise-trimmed — but we still apply a relative cutoff between the
# top hit and the rest in case the FTS side adds weak matches.
REL_KEEP_RATIO = 0.55
ABS_MIN_SCORE = 0.18


def hybrid_search(store: SqliteStore, query: str, *, limit: int = 20, city: str | None = None) -> dict[str, Any]:
    embed = get_embedding_provider()
    llm = get_llm_provider()

    fts_hits = store.search(query, limit=limit, city=city)
    # Use embed_query when the provider distinguishes (e5 prefixes 'query:')
    if hasattr(embed, "embed_query"):
        qvec = embed.embed_query(query)
    else:
        qvec = embed.embed([query])[0]
    vec_hits = store.vector_search(qvec, limit=limit, city=city)

    merged: dict[str, dict[str, Any]] = {}
    for h in fts_hits:
        merged[h["note_id"]] = {
            "note_id": h["note_id"],
            "score": h["score"] * 0.55,
            "matched_chunks": list(h.get("matched_chunks", [])),
        }
    for h in vec_hits:
        nid = h["note_id"]
        if nid in merged:
            merged[nid]["score"] += h["score"] * 0.45
            for s in h.get("matched_chunks", []):
                if s not in merged[nid]["matched_chunks"]:
                    merged[nid]["matched_chunks"].append(s)
        else:
            merged[nid] = {
                "note_id": nid,
                "score": h["score"] * 0.45,
                "matched_chunks": list(h.get("matched_chunks", [])),
            }

    ordered = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    if ordered:
        top = ordered[0]["score"]
        cutoff = max(ABS_MIN_SCORE, top * REL_KEEP_RATIO)
        kept = [h for h in ordered if h["score"] >= cutoff]
        # Always keep the #1 hit even if it is below ABS_MIN_SCORE so the UI
        # surfaces *something* on weak but non-empty queries.
        if not kept:
            kept = ordered[:1]
        ordered = kept[:limit]
    else:
        ordered = []
    notes = []
    for h in ordered:
        n = store.get_note(h["note_id"])
        if n:
            notes.append({
                "note": n,
                "score": round(h["score"], 4),
                "matched_chunks": h["matched_chunks"][:4],
            })
    explanation = llm.explain_search(query, notes)
    return {"query": query, "hits": notes, "explanation": explanation}
