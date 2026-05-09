"""Inbox routes — backfill workflow Stage 2/3 surface."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from ..config import settings
from ..crawler.xhs import CrawlerConfig, CrawlerError, XhsCollectionCrawler
from ..services.inbox_clustering import cluster_inbox
from ..services.inbox_promote import (
    DEFAULT_MAX_PER_REQUEST,
    HARD_MAX_PER_REQUEST,
    promote_notes,
)
from ..store.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


def get_store(request: Request) -> SqliteStore:
    return request.app.state.store


@router.get("")
def list_inbox(
    status: str | None = Query(None),
    cluster_id: str | None = Query(None),
    saved_after: str | None = Query(None),
    saved_before: str | None = Query(None),
    title_query: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    items = store.list_inbox(
        status=status,
        cluster_id=cluster_id,
        saved_after=saved_after,
        saved_before=saved_before,
        title_query=title_query,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/stats")
def stats(store: SqliteStore = Depends(get_store)) -> dict[str, Any]:
    return {
        "by_status": store.inbox_status_counts(),
        "clusters": store.list_clusters(),
    }


@router.get("/clusters")
def clusters(store: SqliteStore = Depends(get_store)) -> list[dict[str, Any]]:
    return store.list_clusters()


@router.post("/cluster")
def run_clustering(
    payload: dict[str, Any] = Body(default_factory=dict),
    store: SqliteStore = Depends(get_store),
) -> dict[str, Any]:
    target = int(payload.get("target_clusters", 12))
    sample = int(payload.get("sample_size", 300))
    status = payload.get("status", "pending")
    only_unassigned = bool(payload.get("only_unassigned", False))
    em_iterations = int(payload.get("em_iterations", 1))
    min_similarity = float(payload.get("min_similarity", 0.55))
    if target < 2 or target > 30:
        raise HTTPException(400, "target_clusters must be in [2, 30]")
    if sample < 10 or sample > 1000:
        raise HTTPException(400, "sample_size must be in [10, 1000]")
    if em_iterations < 0 or em_iterations > 3:
        raise HTTPException(400, "em_iterations must be in [0, 3]")
    if min_similarity < 0.0 or min_similarity > 1.0:
        raise HTTPException(400, "min_similarity must be in [0.0, 1.0]")
    res = cluster_inbox(
        store,
        status=status,
        target_clusters=target,
        sample_size=sample,
        only_unassigned=only_unassigned,
        em_iterations=em_iterations,
        min_similarity=min_similarity,
    )
    return {
        "clusters": res.clusters,
        "assigned": res.assigned,
        "skipped": res.skipped,
        "duration_s": round(res.duration_s, 2),
        "low_confidence": res.low_confidence,
        "em_moves": res.em_moves,
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

    res = promote_notes(store, note_ids, max_per_request=max_per)
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

    Requires a Chrome instance launched with --remote-debugging-port=<cdp>
    and already logged into xiaohongshu.com. Returns inserted/updated counts.
    """
    target_url = (payload.get("url") or settings.xhs_collection_url or "").strip()
    if not target_url:
        raise HTTPException(
            400,
            "no collection URL configured. Pass body.url or set "
            "XHS_COLLECTION_URL env var.",
        )
    cfg = CrawlerConfig(
        cdp_endpoint=str(payload.get("cdp") or settings.xhs_cdp_endpoint),
        max_idle_scrolls=int(payload.get("idle") or 4),
        scroll_pause_s=float(payload.get("pause") or 1.4),
        max_scroll_iterations=int(payload.get("max_scrolls") or 400),
        debug_dom=False,
    )
    dry_run = bool(payload.get("dry_run"))
    crawler = XhsCollectionCrawler(cfg)
    try:
        result = await crawler.scan(target_url=target_url)
    except CrawlerError as e:
        raise HTTPException(502, f"crawler error: {e}") from e

    if dry_run:
        return {
            "scanned": len(result.items),
            "page_url": result.page_url,
            "dry_run": True,
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
    return {
        "scanned": len(result.items),
        "inserted": counts["inserted"],
        "updated": counts["updated"],
        "page_url": result.page_url,
        "by_status": store.inbox_status_counts(),
    }