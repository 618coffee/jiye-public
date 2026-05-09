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
from typing import Any

from ..providers import get_embedding_provider, get_llm_provider
from ..store import KnowledgeStore


@dataclass
class ClusterRunResult:
    clusters: list[dict[str, Any]]  # [{id, name, description, count}]
    assigned: int
    skipped: int
    duration_s: float


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
    sample_size: int = 200,
    only_unassigned: bool = False,
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

    # 2c. Nearest-centroid assignment.
    assignments: dict[str, list[str]] = {c["id"]: [] for c in proposed}
    skipped = 0
    for row, vec in zip(rows, title_vecs):
        if not vec:
            skipped += 1
            continue
        best_id, best_score = None, -2.0
        for c, centroid in zip(proposed, centroids):
            score = _cosine(vec, centroid)
            if score > best_score:
                best_score = score
                best_id = c["id"]
        if best_id is not None:
            assignments[best_id].append(row["note_id"])

    # ---- Persist ----
    cluster_records: list[dict[str, Any]] = []
    for c in proposed:
        members = assignments.get(c["id"], [])
        cluster_records.append({
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "count": len(members),
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
    )
