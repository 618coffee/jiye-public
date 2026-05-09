"""Hybrid search: FTS5 BM25 + vector cosine, weighted-merge."""
from __future__ import annotations

from typing import Any

from ..providers import get_embedding_provider, get_llm_provider
from ..store.sqlite_store import SqliteStore


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

    ordered = sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:limit]
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
