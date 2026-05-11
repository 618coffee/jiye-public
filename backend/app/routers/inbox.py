"""Inbox routes — backfill workflow Stage 2/3 surface."""
from __future__ import annotations

from typing import Any
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from ..config import settings
from ..crawler.xhs import CrawlerConfig, CrawlerError, XhsCollectionCrawler
from ..services.inbox_clustering import assign_to_existing_clusters, cluster_inbox
from ..services.inbox_promote import (
    DEFAULT_MAX_PER_REQUEST,
    HARD_MAX_PER_REQUEST,
    promote_notes,
)
from ..store.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


def get_store(request: Request) -> SqliteStore:
    return request.app.state.store


def _detail_tags(item: dict[str, Any]) -> list[str]:
    meta = item.get("raw_meta") or {}
    detail = meta.get("detail") if isinstance(meta, dict) else None
    if not isinstance(detail, dict) or not detail.get("ok"):
        return []
    tags = detail.get("tags") or []
    if not isinstance(tags, list):
        return []
    return [str(t).strip() for t in tags if str(t).strip()]


def _filter_detail_tags(items: list[dict[str, Any]], tags: list[str]) -> list[dict[str, Any]]:
    selected = {t.strip() for t in tags if t.strip()}
    if not selected:
        return items
    return [it for it in items if selected.intersection(_detail_tags(it))]


@router.get("")
def list_inbox(
    status: str | None = Query(None),
    cluster_id: str | None = Query(None),
    detail_tag: list[str] = Query(default=[]),
    saved_after: str | None = Query(None),
    saved_before: str | None = Query(None),
    title_query: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    tag_filter = [t for t in detail_tag if t]
    fetch_limit = 10_000 if tag_filter else limit
    fetch_offset = 0 if tag_filter else offset
    # Explicit cluster filter (not __none__) goes through the multi-tag join
    # so a note tagged with multiple clusters appears under each of them.
    if cluster_id and cluster_id != "__none__":
        items = store.list_inbox_by_cluster_any(
            cluster_id,
            status=status,
            limit=fetch_limit,
            offset=fetch_offset,
        )
        # Apply remaining filters in-memory (saved_at / title_query). These
        # are rarely combined with cluster filtering and the result set is
        # already capped by limit; trade-off favours code simplicity.
        if saved_after:
            items = [it for it in items if (it.get("saved_at") or "") >= saved_after]
        if saved_before:
            items = [it for it in items if (it.get("saved_at") or "") <= saved_before]
        if title_query:
            q = title_query.lower()
            items = [it for it in items if q in (it.get("title") or "").lower()]
    else:
        items = store.list_inbox(
            status=status,
            cluster_id=cluster_id,
            saved_after=saved_after,
            saved_before=saved_before,
            title_query=title_query,
            limit=fetch_limit,
            offset=fetch_offset,
        )
    if tag_filter:
        items = _filter_detail_tags(items, tag_filter)
        items = items[offset: offset + limit]
    if items:
        tags_by_id = store.get_note_tags_bulk([it["note_id"] for it in items])
        for it in items:
            it["tags"] = tags_by_id.get(it["note_id"], [])
            it["detail_tags"] = _detail_tags(it)
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/stats")
def stats(store: SqliteStore = Depends(get_store)) -> dict[str, Any]:
    return {
        "by_status": store.inbox_status_counts(),
        "clusters": store.list_clusters(),
    }


@router.get("/detail-tags")
def detail_tags(
    status: str | None = Query(None),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    items = store.list_inbox(status=status, limit=10_000, offset=0)
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(_detail_tags(item))
    return {
        "tags": [
            {"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    }


@router.get("/clusters")
def clusters(store: SqliteStore = Depends(get_store)) -> list[dict[str, Any]]:
    return store.list_clusters()


@router.post("/cluster")
def run_clustering(
    payload: dict[str, Any] = Body(default_factory=dict),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    """Re-cluster the inbox.

    No tunables exposed to users: the LLM decides the cluster count, and
    sampling / threshold / EM defaults live in the service module. The
    only knob still honoured is ``status`` and ``only_unassigned`` (used
    internally), which are not surfaced in the UI.
    """
    status = payload.get("status", "pending")
    only_unassigned = bool(payload.get("only_unassigned", False))
    res = cluster_inbox(
        store,
        status=status,
        only_unassigned=only_unassigned,
    )
    return {
        "clusters": res.clusters,
        "assigned": res.assigned,
        "skipped": res.skipped,
        "duration_s": round(res.duration_s, 2),
        "low_confidence": res.low_confidence,
        "em_moves": res.em_moves,
        "multi_tagged": res.multi_tagged,
    }


@router.post("/discard")
def discard(
    payload: dict[str, Any] = Body(...),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    note_ids = payload.get("note_ids") or []
    if not isinstance(note_ids, list) or not note_ids:
        raise HTTPException(400, "note_ids (non-empty list) required")
    store.set_inbox_status(note_ids, "discarded")
    return {"discarded": len(note_ids)}


@router.post("/promote")
def promote(
    payload: dict[str, Any] = Body(...),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    """Run the detail-crawl + LLM ingest pipeline for the given inbox rows.

    Body:
      - note_ids:       list[str]    required, non-empty
      - max_per_request:int          optional, default 20, hard cap 50
      - allow_spa_fallback: bool     optional, default false; clicking the
        logged-in xhs page is riskier and can trigger the QR wall

    Response:
      - results:    list[{note_id, status, error?, title?}]
      - promoted:   int
      - failed:     int
      - skipped:    int   (over-cap entries; resubmit to continue)
      - duration_s: float
    """
    note_ids = payload.get("note_ids") or []
    if not isinstance(note_ids, list) or not note_ids:
        raise HTTPException(400, "note_ids (non-empty list) required")
    if not all(isinstance(x, str) and x for x in note_ids):
        raise HTTPException(400, "note_ids must be a list of non-empty strings")

    max_per = int(payload.get("max_per_request") or DEFAULT_MAX_PER_REQUEST)
    if max_per < 1 or max_per > HARD_MAX_PER_REQUEST:
        raise HTTPException(
            400, f"max_per_request must be in [1, {HARD_MAX_PER_REQUEST}]"
        )

    allow_spa_fallback = bool(payload.get("allow_spa_fallback") or False)
    res = promote_notes(
        store,
        note_ids,
        max_per_request=max_per,
        allow_spa_fallback=allow_spa_fallback,
    )
    return {
        "results": [
            {
                "note_id": r.note_id,
                "status": r.status,
                "error": r.error,
                "title": r.title,
            }
            for r in res.results
        ],
        "promoted": res.promoted,
        "failed": res.failed,
        "skipped": res.skipped,
        "duration_s": round(res.duration_s, 2),
    }


@router.post("/scan")
async def scan_collection(
    payload: dict[str, Any] = Body(default_factory=dict),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    """Drive the xhs collection crawler and upsert into note_inbox.

    Body (all optional):
      - url:          override the collection page URL (defaults to settings.xhs_collection_url)
      - cdp:          override the CDP endpoint (defaults to settings.xhs_cdp_endpoint)
      - idle:         max idle scrolls (default 4)
      - pause:        scroll pause seconds (default 1.4)
      - max_scrolls:  scroll cap (default 400)
      - dry_run:      bool — if true, returns counts but doesn't upsert
      - full:         bool — if true, disables incremental early-exit and
                      scrolls all the way to the bottom (use to backfill
                      after a long gap or when you suspect missed items).
                      Default False = incremental: stop after a short
                      streak of all-already-known items.

    Requires a Chrome instance launched with --remote-debugging-port=<cdp>
    and already logged into xiaohongshu.com. Returns inserted/updated counts.
    """
    target_url = (payload.get("url") or settings.xhs_collection_url or "").strip() or None
    cfg = CrawlerConfig(
        cdp_endpoint=str(payload.get("cdp") or settings.xhs_cdp_endpoint),
        max_idle_scrolls=int(payload.get("idle") or 4),
        scroll_pause_s=float(payload.get("pause") or 1.4),
        max_scroll_iterations=int(payload.get("max_scrolls") or 400),
        debug_dom=False,
    )
    dry_run = bool(payload.get("dry_run"))
    full_scan = bool(payload.get("full"))
    # Incremental: hand the crawler our existing inbox ids so it can stop
    # early once the visible feed is entirely "old". Skipped when full=true
    # or when the inbox is empty (nothing to dedupe against → cold start).
    known_ids: set[str] | None = None
    pre_scan_ids: set[str] = set()
    try:
        pre_scan_ids = store.all_inbox_ids()
    except Exception:
        pre_scan_ids = set()
    if not full_scan:
        known_ids = pre_scan_ids if pre_scan_ids else None

    crawler = XhsCollectionCrawler(cfg)
    try:
        result = await crawler.scan(target_url=target_url, known_note_ids=known_ids)
    except CrawlerError as e:
        raise HTTPException(502, f"crawler error: {e}") from e

    if dry_run:
        return {
            "scanned": len(result.items),
            "page_url": result.page_url,
            "dry_run": True,
            "early_stopped": result.early_stopped,
        }

    inbox_payload = [
        {
            "note_id": it["note_id"],
            "url": it["url"],
            "title": it.get("title"),
            "cover_url": it.get("cover_url"),
            "author": it.get("author"),
            "saved_at": None,
            "raw_meta": {
                "discovered_via": "xhs_collection_crawler",
                "page_url": result.page_url,
                "xsec_token": it.get("xsec_token"),
                "xsec_source": it.get("xsec_source") or "pc_user",
            },
        }
        for it in result.items
    ]
    counts = store.upsert_inbox_many(inbox_payload)

    # Auto-assign newly-inserted notes to nearest existing cluster.
    # Never raises: anything weird → notes get tagged "未聚类" so they're
    # still visible and will be folded back in on the next full re-cluster.
    new_ids = [it["note_id"] for it in result.items if it["note_id"] not in pre_scan_ids]
    auto_cluster: dict[str, Any] = {"assigned": 0, "unclustered": 0}
    if new_ids:
        try:
            auto_cluster = assign_to_existing_clusters(store, new_ids)
        except Exception as e:  # belt-and-suspenders; service already swallows
            auto_cluster = {"assigned": 0, "unclustered": len(new_ids), "reason": f"unexpected: {e}"}

    return {
        "scanned": len(result.items),
        "inserted": counts["inserted"],
        "updated": counts["updated"],
        "page_url": result.page_url,
        "early_stopped": result.early_stopped,
        "incremental": known_ids is not None,
        "by_status": store.inbox_status_counts(),
        "auto_cluster": auto_cluster,
    }
