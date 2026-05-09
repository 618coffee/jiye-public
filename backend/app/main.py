"""FastAPI app entry."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .store.sqlite_store import SqliteStore
from .routers import cities, notes, search, settings as settings_router, inbox


def get_store() -> SqliteStore:
    return SqliteStore(settings.db_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    store.init_schema()
    # Make sure OCR demo sample images exist before seeding (seed pipeline
    # references them via http://localhost:8000/sampleimg/...).
    from .services.seed_images import ensure_samples
    ensure_samples()
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
app.include_router(inbox.router)

# Serve cached thumbnails
_thumb_dir = Path(settings.data_dir).resolve() / "thumb"
_thumb_dir.mkdir(parents=True, exist_ok=True)
app.mount("/thumbs", StaticFiles(directory=str(_thumb_dir)), name="thumbs")

# Serve generated demo sample images (with real text for OCR)
_seed_img_dir = Path(settings.data_dir).resolve() / "raw_seed"
_seed_img_dir.mkdir(parents=True, exist_ok=True)
app.mount("/sampleimg", StaticFiles(directory=str(_seed_img_dir)), name="sampleimg")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
