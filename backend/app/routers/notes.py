from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..store.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/notes", tags=["notes"])


def get_store(request: Request) -> SqliteStore:
    return request.app.state.store


@router.get("/{note_id}")
def get_note(note_id: str, store: SqliteStore = Depends(get_store)) -> dict:
    n = store.get_note(note_id)
    if not n:
        raise HTTPException(404, "Note not found")
    return n
