from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Request

from ..services.search import hybrid_search
from ..store.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/search", tags=["search"])


def get_store(request: Request) -> SqliteStore:
    return request.app.state.store


@router.get("")
def search(
    q: str,
    limit: int = 20,
    city: Optional[str] = None,
    store: SqliteStore = Depends(get_store),
) -> dict:
    return hybrid_search(store, q, limit=limit, city=city)
