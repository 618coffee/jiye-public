"""Inbox routes — backfill workflow Stage 2/3 surface."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from ..services.inbox_clustering import cluster_inbox
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
    sample = int(payload.get("sample_size", 200))
    status = payload.get("status", "pending")
    only_unassigned = bool(payload.get("only_unassigned", False))
    if target < 2 or target > 30:
        raise HTTPException(400, "target_clusters must be in [2, 30]")
    if sample < 10 or sample > 1000:
        raise HTTPException(400, "sample_size must be in [10, 1000]")
    res = cluster_inbox(
        store,
        status=status,
        target_clusters=target,
        sample_size=sample,
        only_unassigned=only_unassigned,
    )
    return {
        "clusters": res.clusters,
        "assigned": res.assigned,
        "skipped": res.skipped,
        "duration_s": round(res.duration_s, 2),
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
