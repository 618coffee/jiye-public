"""Two-phase clustering for the inbox.

Phase 1 (LLM): take a representative sample of N titles, ask the LLM to
              propose ~target_clusters thematic buckets with sample titles.
Phase 2 (e5):  embed every cluster's sample_titles -> centroid; embed every
              remaining inbox title -> nearest-centroid by cosine; assign.

Why two-phase?
- LLM alone on 1000+ titles is expensive and exceeds context.
- Embedding alone (k-means etc.) gives no human-readable labels.
- This combo: humans get nice Chinese labels, machine handles the long tail.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..providers import get_embedding_provider, get_llm_provider
from ..store import KnowledgeStore


def _time_aggregates(store: KnowledgeStore, note_ids: list[str]) -> dict[str, Any]:
    """Compute time-aggregate fields for a cluster of note IDs.

    Returns a dict with ``earliest_discovered_at``, ``latest_discovered_at``,
    ``earliest_saved_at``, ``latest_saved_at``, ``notes_added_last_7d``.
    All fields default to ``None``/``0`` when no underlying data exists
    (e.g. ``saved_at`` is unpopulated for current xhs feed scans).
    """
    if not note_ids or not hasattr(store, "get_inbox_times"):
        return {
            "earliest_discovered_at": None,
            "latest_discovered_at": None,
            "earliest_saved_at": None,
            "latest_saved_at": None,
            "notes_added_last_7d": 0,
        }
    times = store.get_inbox_times(note_ids)  # type: ignore[attr-defined]
    discs = [d for d, _ in times if d]
    saves = [s for _, s in times if s]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return {
        "earliest_discovered_at": min(discs) if discs else None,
        "latest_discovered_at": max(discs) if discs else None,
        "earliest_saved_at": min(saves) if saves else None,
        "latest_saved_at": max(saves) if saves else None,
        "notes_added_last_7d": sum(1 for d in discs if d >= cutoff),
    }


@dataclass
class ClusterRunResult:
    clusters: list[dict[str, Any]]  # [{id, name, description, count}]
    assigned: int
    skipped: int
    duration_s: float
    low_confidence: int = 0  # titles routed to __low_conf__ catch-all
    em_moves: int = 0        # titles that changed cluster during EM refinement


def _cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    # Both e5 and our store vectors are L2-normalised, so this is exact cosine.
    return s


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("cannot take centroid of empty vector list")
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += x
    n = float(len(vectors))
    out = [x / n for x in out]
    # Re-normalise so cosine remains a clean dot product.
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


def _sample_titles(items: list[dict[str, Any]], n: int, seed: int = 17) -> list[dict[str, Any]]:
    """Stratified-ish sample: deterministic shuffle + take first N.
    Good enough for free-form titles; we don't need true stratification because
    the LLM will reorganise everything anyway.
    """
    if len(items) <= n:
        return list(items)
    rng = random.Random(seed)
    pool = list(items)
    rng.shuffle(pool)
    return pool[:n]


def cluster_inbox(
    store: KnowledgeStore,
    *,
    status: str = "pending",
    target_clusters: int = 12,
    sample_size: int = 300,
    only_unassigned: bool = False,
    em_iterations: int = 1,
    min_similarity: float = 0.55,
) -> ClusterRunResult:
    """End-to-end: pull inbox, build clusters, write assignments.

    - `status`: which inbox status to cluster (typically 'pending').
    - `target_clusters`: rough number of buckets the LLM should produce.
    - `sample_size`: how many titles the LLM sees in phase 1.
    - `only_unassigned`: if True, skip rows that already have a cluster_id.
    """
    t0 = time.time()
    rows = store.list_inbox(status=status, limit=10_000)
    if only_unassigned:
        rows = [r for r in rows if not r.get("cluster_id")]
    if not rows:
        return ClusterRunResult(clusters=[], assigned=0, skipped=0, duration_s=time.time() - t0)

    titles = [r.get("title") or "" for r in rows]

    # ---- Phase 1: LLM proposes labelled buckets from a sample ----
    sample_rows = _sample_titles(rows, sample_size)
    sample_titles = [r.get("title") or "" for r in sample_rows if (r.get("title") or "").strip()]
    if not sample_titles:
        return ClusterRunResult(clusters=[], assigned=0, skipped=len(rows), duration_s=time.time() - t0)

    llm = get_llm_provider()
    proposed = llm.cluster_titles(sample_titles, target_clusters=target_clusters)

    # Make sure every cluster has an id; LLM impl should already, but defend.
    for c in proposed:
        c.setdefault("id", uuid.uuid4().hex[:8])
        c.setdefault("name", "未命名")
        c.setdefault("description", "")
        c.setdefault("sample_titles", [])

    # ---- Phase 2: embedding-based assignment ----
    embed = get_embedding_provider()

    # 2a. Build a centroid per cluster from its sample_titles. If a cluster
    # has no usable samples, fall back to embedding the cluster name itself.
    centroid_inputs: list[list[str]] = []
    for c in proposed:
        srcs = [t for t in c["sample_titles"] if isinstance(t, str) and t.strip()]
        if not srcs:
            srcs = [c["name"]]
        centroid_inputs.append(srcs)

    # Flatten + embed in one batch for speed.
    flat: list[str] = []
    spans: list[tuple[int, int]] = []  # (start, end) per cluster
    for srcs in centroid_inputs:
        start = len(flat)
        flat.extend(srcs)
        spans.append((start, len(flat)))
    flat_vecs = embed.embed(flat) if flat else []

    centroids: list[list[float]] = []
    for s, e in spans:
        centroids.append(_centroid(flat_vecs[s:e]))

    # 2b. Embed all inbox titles (in chunks to keep batch sane).
    chunk = 256
    title_vecs: list[list[float]] = []
    for i in range(0, len(titles), chunk):
        title_vecs.extend(embed.embed(titles[i : i + chunk]))

    # 2c. Nearest-centroid assignment. Track score+vector per note for EM/threshold passes.
    # per_note: note_id -> (cluster_idx, score, vec)
    per_note: dict[str, tuple[int, float, list[float]]] = {}
    skipped = 0
    for row, vec in zip(rows, title_vecs):
        if not vec:
            skipped += 1
            continue
        best_idx, best_score = -1, -2.0
        for idx, centroid in enumerate(centroids):
            score = _cosine(vec, centroid)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx >= 0:
            per_note[row["note_id"]] = (best_idx, best_score, vec)

    # ---- Phase 2.5: EM refinement (recompute centroids from assigned vectors) ----
    em_moves = 0
    for _ in range(max(0, em_iterations)):
        # Recompute centroid for each cluster with >=3 assigned members.
        new_centroids = list(centroids)
        for idx in range(len(proposed)):
            members = [v for (ci, _s, v) in per_note.values() if ci == idx]
            if len(members) >= 3:
                try:
                    new_centroids[idx] = _centroid(members)
                except ValueError:
                    pass
        # Re-assign every note to nearest refined centroid.
        moves = 0
        for nid, (old_idx, _old_s, vec) in list(per_note.items()):
            best_idx, best_score = -1, -2.0
            for idx, centroid in enumerate(new_centroids):
                score = _cosine(vec, centroid)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx != old_idx:
                moves += 1
            per_note[nid] = (best_idx, best_score, vec)
        em_moves += moves
        centroids = new_centroids

    # ---- Phase 3: Low-confidence threshold -> catch-all cluster ----
    LOW_CONF_ID = "__low_conf__"
    assignments: dict[str, list[str]] = {c["id"]: [] for c in proposed}
    assignments[LOW_CONF_ID] = []
    low_confidence = 0
    for nid, (idx, score, _vec) in per_note.items():
        if score < min_similarity:
            assignments[LOW_CONF_ID].append(nid)
            low_confidence += 1
        else:
            assignments[proposed[idx]["id"]].append(nid)

    # ---- Persist ----
    cluster_records: list[dict[str, Any]] = []
    for c in proposed:
        members = assignments.get(c["id"], [])
        if members:
            agg = _time_aggregates(store, members)
            cluster_records.append({
                "id": c["id"],
                "name": c["name"],
                "description": c["description"],
                "count": len(members),
                **agg,
            })
    if low_confidence > 0:
        members = assignments[LOW_CONF_ID]
        agg = _time_aggregates(store, members)
        cluster_records.append({
            "id": LOW_CONF_ID,
            "name": "其他（低置信度）",
            "description": "e5 余弦相似度低于阈值的零散收藏",
            "count": low_confidence,
            **agg,
        })
    store.replace_clusters(cluster_records)

    total_assigned = 0
    for cid, ids in assignments.items():
        if ids:
            store.set_inbox_cluster(ids, cid)
            total_assigned += len(ids)

    return ClusterRunResult(
        clusters=cluster_records,
        assigned=total_assigned,
        skipped=skipped,
        duration_s=time.time() - t0,
        low_confidence=low_confidence,
        em_moves=em_moves,
    )
