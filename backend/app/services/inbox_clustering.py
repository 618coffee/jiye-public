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
    multi_tagged: int = 0    # notes that got >=2 tags


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

    # CRITICAL: never proceed with an empty proposal. If the LLM (or its
    # fallback) returned nothing, replace_clusters([]) below would wipe the
    # cluster table AND cascade-delete every note_cluster_tags row, leaving
    # the user with no tags at all. Bail out early so existing assignments
    # are preserved.
    if not proposed:
        return ClusterRunResult(
            clusters=[],
            assigned=0,
            skipped=len(rows),
            duration_s=time.time() - t0,
        )

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
    # Track membership for cluster-row count aggregation only; actual
    # per-note assignment happens via set_note_tags below.
    membership: dict[str, list[str]] = {c["id"]: [] for c in proposed}
    membership[LOW_CONF_ID] = []
    low_confidence = 0
    multi_tagged = 0
    note_tag_lists: list[tuple[str, list[tuple[str, float]]]] = []  # written after replace_clusters
    for nid, (idx, score, vec) in per_note.items():
        if score < min_similarity:
            membership[LOW_CONF_ID].append(nid)
            low_confidence += 1
            note_tag_lists.append((nid, [(LOW_CONF_ID, score)]))
            continue
        # Score against all centroids and keep top-K above secondary threshold.
        scored = [
            (proposed[i]["id"], _cosine(vec, c)) for i, c in enumerate(centroids)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top_score = scored[0][1]
        tags: list[tuple[str, float]] = [scored[0]]
        for cid, sc in scored[1:]:
            if len(tags) >= MAX_TAGS_PER_NOTE:
                break
            if sc < SECONDARY_MIN_SIMILARITY:
                break
            if (top_score - sc) > SECONDARY_MAX_DELTA_FROM_TOP:
                break
            tags.append((cid, sc))
        membership[tags[0][0]].append(nid)
        if len(tags) >= 2:
            multi_tagged += 1
        note_tag_lists.append((nid, tags))

    # ---- Persist ----
    cluster_records: list[dict[str, Any]] = []
    for c in proposed:
        members = membership.get(c["id"], [])
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
        members = membership[LOW_CONF_ID]
        agg = _time_aggregates(store, members)
        cluster_records.append({
            "id": LOW_CONF_ID,
            "name": "其他（低置信度）",
            "description": "e5 余弦相似度低于阈值的零散收藏",
            "count": low_confidence,
            **agg,
        })
    store.replace_clusters(cluster_records)

    # Write multi-tag rows AFTER replace_clusters so the dangling-tag cleanup
    # in replace_clusters doesn't immediately wipe what we just inserted.
    total_assigned = 0
    for nid, tags in note_tag_lists:
        try:
            store.set_note_tags(nid, tags)  # type: ignore[attr-defined]
            total_assigned += 1
        except Exception:
            pass

    return ClusterRunResult(
        clusters=cluster_records,
        assigned=total_assigned,
        skipped=skipped,
        duration_s=time.time() - t0,
        low_confidence=low_confidence,
        em_moves=em_moves,
        multi_tagged=multi_tagged,
    )


# ---------------------------------------------------------------------------
# Incremental: assign newly-scanned notes to the nearest existing cluster.
# Falls back to a synthetic ``__unclustered__`` bucket on any failure
# (no embedding provider, no clusters yet, LLM/network errors, etc.) so
# the user never silently loses notes — they're visible under a "未聚类"
# label and will be folded back in on the next full re-cluster.
# ---------------------------------------------------------------------------

UNCLUSTERED_ID = "__unclustered__"
UNCLUSTERED_NAME = "未聚类"
UNCLUSTERED_DESC = "新增时未能自动归类（聚类服务暂不可用或无现有聚类）。下次「重新聚类」时会被重新分配。"

# Multi-label tagging thresholds.
MAX_TAGS_PER_NOTE = 2
PRIMARY_MIN_SIMILARITY = 0.55  # top-1 must clear this to escape __unclustered__
SECONDARY_MIN_SIMILARITY = 0.48  # absolute floor for secondary tags
# Secondary tags must also be within this absolute cosine-distance of the
# top-1 score. e5 embeddings on Chinese short titles cluster very tightly
# in [0.85, 0.95], so an absolute floor alone is meaningless; the relative
# margin is what actually keeps off-topic clusters from sneaking in.
SECONDARY_MAX_DELTA_FROM_TOP = 0.015


def _mark_unclustered(store: KnowledgeStore, note_ids: list[str], reason: str) -> dict[str, Any]:
    """Tag every note_id as ``__unclustered__`` and ensure the bucket exists."""
    if not note_ids:
        return {"assigned": 0, "unclustered": 0, "multi_tagged": 0, "reason": reason}
    for nid in note_ids:
        try:
            store.set_note_tags(nid, [(UNCLUSTERED_ID, 0.0)])  # type: ignore[attr-defined]
        except Exception:
            store.set_inbox_cluster([nid], UNCLUSTERED_ID)
    try:
        store.upsert_cluster({  # type: ignore[attr-defined]
            "id": UNCLUSTERED_ID,
            "name": UNCLUSTERED_NAME,
            "description": UNCLUSTERED_DESC,
        })
    except Exception:
        pass  # cluster bucket is cosmetic; the note_inbox.cluster_id is what matters
    return {"assigned": 0, "unclustered": len(note_ids), "multi_tagged": 0, "reason": reason}


def build_existing_centroids(
    store: KnowledgeStore,
    embed,
    *,
    sample_per_cluster: int = 30,
) -> tuple[list[str], list[list[float]], list[dict[str, Any]]]:
    """Build a centroid per existing (non-unclustered) cluster.

    Returns ``(cluster_ids, centroids, existing_meta)`` where ``cluster_ids[i]``
    corresponds to ``centroids[i]``. Empty clusters fall back to embedding the
    cluster name itself, which keeps the centroid count aligned with metadata.
    Shared between :func:`assign_to_existing_clusters` and the backfill script.
    """
    existing = [c for c in store.list_clusters() if c["id"] != UNCLUSTERED_ID]
    if not existing:
        return [], [], []

    cluster_titles: list[tuple[str, list[str]]] = []
    for c in existing:
        cid = c["id"]
        try:
            members = store.list_inbox(
                cluster_id=cid, status=None, limit=sample_per_cluster
            )
        except Exception:
            members = []
        titles = [m.get("title") or "" for m in members if (m.get("title") or "").strip()]
        if not titles:
            titles = [c.get("name") or cid]
        cluster_titles.append((cid, titles))

    flat: list[str] = []
    spans: list[tuple[int, int]] = []
    for _cid, titles in cluster_titles:
        start = len(flat)
        flat.extend(titles)
        spans.append((start, len(flat)))
    all_vecs = embed.embed(flat) if flat else []

    valid_ids: list[str] = []
    centroids: list[list[float]] = []
    for (cid, _t), (s, e) in zip(cluster_titles, spans):
        if e > s:
            valid_ids.append(cid)
            centroids.append(_centroid(all_vecs[s:e]))
    return valid_ids, centroids, existing


def assign_to_existing_clusters(
    store: KnowledgeStore,
    new_note_ids: list[str],
    *,
    min_similarity: float = PRIMARY_MIN_SIMILARITY,
    sample_per_cluster: int = 30,
) -> dict[str, Any]:
    """Classify *new* inbox notes into the nearest existing cluster(s).

    Designed for the post-scan path: cheap (no LLM), embedding-only, and
    never throws — anything that goes wrong (no provider, no clusters,
    embedding error) results in the notes being tagged ``__unclustered__``
    so a future full re-cluster can sweep them up.

    Returns ``{assigned, unclustered, multi_tagged, reason?, by_cluster?}``
    where ``multi_tagged`` is the count of notes that received >=2 tags.
    """
    if not new_note_ids:
        return {"assigned": 0, "unclustered": 0, "multi_tagged": 0}

    try:
        embed = get_embedding_provider()
    except Exception as e:
        return _mark_unclustered(store, new_note_ids, f"no embedding provider: {e}")

    try:
        valid_cluster_ids, centroids, existing = build_existing_centroids(
            store, embed, sample_per_cluster=sample_per_cluster,
        )
    except Exception as e:
        return _mark_unclustered(store, new_note_ids, f"centroid build failed: {e}")
    if not centroids:
        return _mark_unclustered(store, new_note_ids, "no existing clusters yet")

    try:
        new_rows = [store.get_inbox(nid) for nid in new_note_ids]
    except Exception as e:
        return _mark_unclustered(store, new_note_ids, f"get_inbox failed: {e}")
    new_pairs = [(r["note_id"], (r.get("title") or "").strip()) for r in new_rows if r]
    new_pairs = [(nid, t) for nid, t in new_pairs if t]
    if not new_pairs:
        return {"assigned": 0, "unclustered": 0, "multi_tagged": 0,
                "reason": "no titles to embed"}

    try:
        new_vecs = embed.embed([t for _nid, t in new_pairs])
    except Exception as e:
        return _mark_unclustered(
            store, [nid for nid, _ in new_pairs], f"embedding failed: {e}",
        )

    by_cluster: dict[str, list[str]] = {}
    unclustered_ids: list[str] = []
    multi_tagged = 0
    assigned = 0
    for (nid, _title), vec in zip(new_pairs, new_vecs):
        if not vec:
            unclustered_ids.append(nid)
            continue
        scored = [
            (valid_cluster_ids[idx], _cosine(vec, centroid))
            for idx, centroid in enumerate(centroids)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        if not scored or scored[0][1] < min_similarity:
            unclustered_ids.append(nid)
            continue
        top_score = scored[0][1]
        tags: list[tuple[str, float]] = [scored[0]]
        for cid, score in scored[1:]:
            if len(tags) >= MAX_TAGS_PER_NOTE:
                break
            if score < SECONDARY_MIN_SIMILARITY:
                break
            if (top_score - score) > SECONDARY_MAX_DELTA_FROM_TOP:
                break
            tags.append((cid, score))
        try:
            store.set_note_tags(nid, tags)  # type: ignore[attr-defined]
        except Exception:
            unclustered_ids.append(nid)
            continue
        by_cluster.setdefault(tags[0][0], []).append(nid)
        assigned += 1
        if len(tags) >= 2:
            multi_tagged += 1

    # Refresh count on affected clusters (cosmetic).
    for cid in by_cluster:
        meta = next((c for c in existing if c["id"] == cid), None)
        if meta is not None:
            try:
                store.upsert_cluster({  # type: ignore[attr-defined]
                    "id": cid,
                    "name": meta.get("name") or cid,
                    "description": meta.get("description"),
                })
            except Exception:
                pass

    if unclustered_ids:
        _mark_unclustered(store, unclustered_ids, "low confidence or assignment error")

    out: dict[str, Any] = {
        "assigned": assigned,
        "unclustered": len(unclustered_ids),
        "multi_tagged": multi_tagged,
    }
    if by_cluster:
        out["by_cluster"] = {cid: len(ids) for cid, ids in by_cluster.items()}
    return out
