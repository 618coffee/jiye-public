"""FastAPI app entry."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .store.sqlite_store import SqliteStore
from .routers import cities, notes, search, settings as settings_router


def get_store() -> SqliteStore:
    return SqliteStore(settings.db_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    store.init_schema()
    # Seed if empty
    rows = store.list_cities()
    if not rows:
        from .seed import seed
        seed(store)
    app.state.store = store
    yield


app = FastAPI(title="XHS Knowledge Base", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cities.router)
app.include_router(notes.router)
app.include_router(search.router)
app.include_router(settings_router.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
