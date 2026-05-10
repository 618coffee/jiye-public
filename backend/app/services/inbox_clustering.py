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

# ---------------------------------------------------------------------------
# Tunables. Defined here so module-level helpers can reference them as
# default args without forward-reference issues.
# ---------------------------------------------------------------------------

UNCLUSTERED_ID = "__unclustered__"
UNCLUSTERED_NAME = "未聚类"
UNCLUSTERED_DESC = "新增时未能自动归类（聚类服务暂不可用或无现有聚类）。下次「重新聚类」时会被重新分配。"

# Multi-label tagging thresholds.
MAX_TAGS_PER_NOTE = 2
# --- Primary-tag confidence ---
# e5 embeddings on Chinese short titles cluster tightly in [0.85, 0.95], so
# an absolute cosine floor is almost meaningless. The real signal is
# *how much* top-1 beats the runner-up (top-2): a confident match has a
# clear gap to the second-best cluster, while a "doesn't fit anywhere" or
# "could be either" title has top-1 ≈ top-2.
#
# Why top1-vs-top2 instead of top1-vs-mean(rest)? When the LLM produces
# several clusters in the same broad domain (e.g. 6 travel-ish clusters),
# the mean of "the rest" is dragged up by all the other in-domain
# centroids, so even a clear top-1 looks like a small gap and gets
# rejected. top-2 only cares about the actual runner-up, which is the
# real ambiguity signal.
#
# A title is considered confidently routable to its top-1 cluster only if
# BOTH hold:
#   (a) top-1 cosine >= PRIMARY_MIN_SIMILARITY  (very low absolute floor;
#       mainly catches dead/empty embeddings)
#   (b) top-1 - top-2 >= PRIMARY_MIN_TOP_GAP  (relative gap)
# Otherwise the title is routed to the low-confidence / unclustered bucket.
PRIMARY_MIN_SIMILARITY = 0.30
PRIMARY_MIN_TOP_GAP = 0.012

# --- Secondary-tag thresholds ---
SECONDARY_MIN_SIMILARITY = 0.48  # absolute floor for secondary tags
# Secondary tag must be within this cosine-distance of the top-1 score.
SECONDARY_MAX_DELTA_FROM_TOP = 0.015


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


def _embed_text(row: dict[str, Any] | None, *, title: str | None = None) -> str:
    """Build the string we hand to the embedding model for a single note.

    Title alone is too thin (8-15 char Chinese strings cluster too tightly in
    e5 space). When ``raw_meta.detail`` is present (filled by
    scripts/enrich_inbox.py), we append the user-curated tag list and the
    leading body excerpt. Format::

        <title>
        标签: tag1,tag2,tag3
        正文: <first ~180 chars of body>

    Falls back to title-only when detail is missing or fetch failed.
    """
    if row is None:
        return (title or "").strip()
    t = ((title if title is not None else row.get("title")) or "").strip()

    # Pull enrichment from raw_meta.detail if present.
    raw_meta = row.get("raw_meta")
    detail: dict[str, Any] | None = None
    if isinstance(raw_meta, str) and raw_meta:
        try:
            import json as _json
            meta = _json.loads(raw_meta)
            d = meta.get("detail")
            if isinstance(d, dict) and d.get("ok"):
                detail = d
        except (ValueError, TypeError):
            detail = None
    elif isinstance(raw_meta, dict):
        d = raw_meta.get("detail")
        if isinstance(d, dict) and d.get("ok"):
            detail = d

    if not detail:
        return t

    parts = [t] if t else []
    tags = detail.get("tags") or []
    if isinstance(tags, list) and tags:
        clean_tags = [str(x).strip() for x in tags if str(x).strip()]
        if clean_tags:
            parts.append("标签: " + ",".join(clean_tags[:12]))
    body = (detail.get("body") or "").strip()
    if body:
        # ~180 chars keeps embeddings affordable; xhs body is mostly the lead.
        parts.append("正文: " + body[:180])
    return "\n".join(parts)


def _is_low_confidence(
    scored_desc: list[float],
    *,
    abs_floor: float = PRIMARY_MIN_SIMILARITY,
    min_gap: float = PRIMARY_MIN_TOP_GAP,
) -> bool:
    """Return True if the top-1 score does not stand out from the runner-up.

    ``scored_desc`` must be a non-empty list of cosine scores sorted desc.
    Uses top-1 vs top-2 gap (not vs mean of all others) so that having
    many in-domain clusters doesn't artificially inflate the rejection
    rate on clearly-matched titles.
    """
    if not scored_desc:
        return True
    top1 = scored_desc[0]
    if top1 < abs_floor:
        return True
    if len(scored_desc) < 2:
        return False  # only one cluster; nothing to compare against
    top2 = scored_desc[1]
    return (top1 - top2) < min_gap


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
    target_clusters: int | None = None,
    sample_size: int = 300,
    only_unassigned: bool = False,
    em_iterations: int = 1,
    min_similarity: float = PRIMARY_MIN_SIMILARITY,
    min_top_gap: float = PRIMARY_MIN_TOP_GAP,
) -> ClusterRunResult:
    """End-to-end: pull inbox, build clusters, write assignments.

    - `status`: which inbox status to cluster (typically 'pending').
    - `target_clusters`: optional hint for how many buckets the LLM should
      produce. ``None`` (default, recommended) lets the LLM pick whatever
      number of clusters fits the data best — could be 1, could be 40.
    - `sample_size`: how many titles the LLM sees in phase 1.
    - `only_unassigned`: if True, skip rows that already have a cluster_id.
    - `min_similarity`: absolute cosine floor; below this -> __low_conf__.
    - `min_top_gap`: top-1 must beat top-2 by at least this margin,
      otherwise the title is treated as low-confidence (ambiguous).
    """
    t0 = time.time()
    rows = store.list_inbox(status=status, limit=10_000)
    if only_unassigned:
        rows = [r for r in rows if not r.get("cluster_id")]
    if not rows:
        return ClusterRunResult(clusters=[], assigned=0, skipped=0, duration_s=time.time() - t0)

    # Embed-input strings carry author when available; titles[] keeps the
    # raw title for downstream display / debugging only.
    titles = [r.get("title") or "" for r in rows]
    embed_inputs = [_embed_text(r) for r in rows]

    # ---- Phase 1: LLM proposes labelled buckets from a sample ----
    sample_rows = _sample_titles(rows, sample_size)
    sample_items = [_embed_text(r) for r in sample_rows if (r.get("title") or "").strip()]
    if not sample_items:
        return ClusterRunResult(clusters=[], assigned=0, skipped=len(rows), duration_s=time.time() - t0)

    llm = get_llm_provider()
    proposed = llm.cluster_titles(sample_items, target_clusters=target_clusters)

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

    # ---- Phase 2: LLM batch classification ----
    # Why not e5 nearest-centroid? Because on 8-15 char Chinese titles e5
    # cosine is too noisy: every centroid scores ~0.85-0.92, and titles whose
    # theme isn't expressible in their literal characters (place names like
    # "丹巴甲居藏寨") get assigned to whichever cluster happens to share a
    # character. The LLM, given the title plus a labelled menu of buckets,
    # picks the right one almost every time.
    #
    # We still embed everything so we can:
    #   (a) fall back to nearest-centroid if the LLM call collapses, and
    #   (b) compute multi-label secondary tags via cosine.
    embed = get_embedding_provider()

    # 2a. Build a centroid per cluster from its sample_titles. If a cluster
    # has no usable samples, fall back to embedding the cluster name itself.
    centroid_inputs: list[list[str]] = []
    for c in proposed:
        srcs = [t for t in c["sample_titles"] if isinstance(t, str) and t.strip()]
        if not srcs:
            srcs = [c["name"]]
        centroid_inputs.append(srcs)

    flat: list[str] = []
    spans: list[tuple[int, int]] = []
    for srcs in centroid_inputs:
        start = len(flat)
        flat.extend(srcs)
        spans.append((start, len(flat)))
    flat_vecs = embed.embed(flat) if flat else []

    centroids: list[list[float]] = []
    for s, e in spans:
        centroids.append(_centroid(flat_vecs[s:e]))

    chunk = 256
    title_vecs: list[list[float]] = []
    for i in range(0, len(embed_inputs), chunk):
        title_vecs.extend(embed.embed(embed_inputs[i : i + chunk]))

    cluster_id_to_idx = {c["id"]: i for i, c in enumerate(proposed)}

    # 2b. Ask the LLM to classify every title against the proposed buckets.
    llm_assignments: list[str | None] = []
    try:
        llm_assignments = llm.classify_titles(embed_inputs, proposed)
    except Exception as exc:
        print(f"[cluster_inbox] classify_titles failed: {exc}; falling back to e5")
        llm_assignments = [None] * len(titles)

    # If the LLM returned None for almost everything, treat the call as
    # broken and fall back to e5 nearest-centroid.
    none_rate = sum(1 for a in llm_assignments if a is None) / max(1, len(llm_assignments))
    use_e5_fallback = none_rate > 0.5
    if use_e5_fallback:
        print(f"[cluster_inbox] classify_titles none-rate {none_rate:.0%} > 50%; falling back to e5")

    # per_note: note_id -> (cluster_idx, primary_score, vec)
    per_note: dict[str, tuple[int, float, list[float]]] = {}
    skipped = 0
    for row, vec, llm_cid in zip(rows, title_vecs, llm_assignments):
        if not vec:
            skipped += 1
            continue
        if not use_e5_fallback and llm_cid is not None:
            idx = cluster_id_to_idx.get(llm_cid, -1)
            if idx >= 0:
                score = _cosine(vec, centroids[idx])
                per_note[row["note_id"]] = (idx, score, vec)
                continue
            # LLM returned an unknown id; fall through to e5 nearest
        if use_e5_fallback or llm_cid is None:
            best_idx, best_score = -1, -2.0
            for idx, centroid in enumerate(centroids):
                score = _cosine(vec, centroid)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx >= 0:
                per_note[row["note_id"]] = (best_idx, best_score, vec)

    # ---- Phase 2.5: EM refinement (only meaningful when we used e5) ----
    em_moves = 0
    if use_e5_fallback:
        for _ in range(max(0, em_iterations)):
            new_centroids = list(centroids)
            for idx in range(len(proposed)):
                members = [v for (ci, _s, v) in per_note.values() if ci == idx]
                if len(members) >= 3:
                    try:
                        new_centroids[idx] = _centroid(members)
                    except ValueError:
                        pass
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

    # ---- Phase 3: Low-confidence + multi-label assignment ----
    LOW_CONF_ID = "__low_conf__"
    membership: dict[str, list[str]] = {c["id"]: [] for c in proposed}
    membership[LOW_CONF_ID] = []
    low_confidence = 0
    multi_tagged = 0
    note_tag_lists: list[tuple[str, list[tuple[str, float]]]] = []

    # Build a quick lookup: note_id -> llm_cid (for the LLM-trust path)
    nid_to_llm_cid: dict[str, str | None] = {
        rows[i]["note_id"]: llm_assignments[i] for i in range(len(rows))
    }

    for nid, (idx, score, vec) in per_note.items():
        # Score against all centroids for multi-label tagging.
        scored = [(proposed[i]["id"], _cosine(vec, c)) for i, c in enumerate(centroids)]
        scored.sort(key=lambda x: x[1], reverse=True)

        if use_e5_fallback:
            # Pure e5 path: use the gap-based confidence test.
            if _is_low_confidence(
                [s for _cid, s in scored], abs_floor=min_similarity, min_gap=min_top_gap,
            ):
                membership[LOW_CONF_ID].append(nid)
                low_confidence += 1
                note_tag_lists.append((nid, [(LOW_CONF_ID, scored[0][1] if scored else 0.0)]))
                continue
            primary_id = scored[0][0]
            primary_score = scored[0][1]
        else:
            # LLM-trust path: the LLM already said which bucket. None -> low_conf.
            llm_cid = nid_to_llm_cid.get(nid)
            if llm_cid is None or llm_cid not in cluster_id_to_idx:
                membership[LOW_CONF_ID].append(nid)
                low_confidence += 1
                note_tag_lists.append((nid, [(LOW_CONF_ID, 0.0)]))
                continue
            primary_id = llm_cid
            primary_score = _cosine(vec, centroids[cluster_id_to_idx[primary_id]])

        # Multi-label: add secondary tags from the cosine ranking that are
        # close to the primary's score (regardless of whether primary came
        # from LLM or e5).
        tags: list[tuple[str, float]] = [(primary_id, primary_score)]
        for cid, sc in scored:
            if cid == primary_id:
                continue
            if len(tags) >= MAX_TAGS_PER_NOTE:
                break
            if sc < SECONDARY_MIN_SIMILARITY:
                break
            if (primary_score - sc) > SECONDARY_MAX_DELTA_FROM_TOP:
                break
            tags.append((cid, sc))
        membership[primary_id].append(nid)
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
# Constants live at the top of the file (above _embed_text); see there.
# ---------------------------------------------------------------------------


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
        # Build enriched embed strings (title + author when present) so the
        # centroid representation matches what we hand to the embedder for
        # incoming notes in `assign_to_existing_clusters`.
        inputs = [_embed_text(m) for m in members if (m.get("title") or "").strip()]
        if not inputs:
            inputs = [c.get("name") or cid]
        cluster_titles.append((cid, inputs))

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
    min_top_gap: float = PRIMARY_MIN_TOP_GAP,
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
    new_pairs = [
        (r["note_id"], _embed_text(r))
        for r in new_rows
        if r and (r.get("title") or "").strip()
    ]
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
        # Combined absolute-floor + relative-gap test. Plain cosine floor
        # is meaningless on e5 short Chinese titles (everything sits in
        # [0.85, 0.95]); the gap vs the rest is what actually identifies
        # "doesn't fit any existing cluster".
        if _is_low_confidence(
            [s for _cid, s in scored], abs_floor=min_similarity, min_gap=min_top_gap,
        ):
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
