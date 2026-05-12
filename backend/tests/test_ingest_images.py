from __future__ import annotations

import os

os.environ["USE_MOCK"] = "true"

from app.providers import embedding as _embed_mod  # noqa: E402
from app.providers import llm as _llm_mod  # noqa: E402
from app.services.ingest import ingest_note  # noqa: E402
from app.routers.inbox import _hydrate_note_media  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


def _reset_provider_singletons():
    _embed_mod._singleton = None
    _llm_mod._singleton = None


def test_ingest_caches_cover_when_images_are_missing(tmp_path, monkeypatch):
    _reset_provider_singletons()
    data_dir = tmp_path / "data"
    raw_seed = data_dir / "raw_seed"
    raw_seed.mkdir(parents=True)

    from PIL import Image
    sample = raw_seed / "cover.jpg"
    Image.new("RGB", (80, 60), color=(210, 60, 80)).save(sample)

    monkeypatch.setattr("app.config.settings.data_dir", str(data_dir))
    monkeypatch.setattr("app.services.ingest.settings.data_dir", str(data_dir))
    monkeypatch.setattr("app.services.ocr.settings.data_dir", str(data_dir))
    monkeypatch.setattr("app.services.ingest.settings.ocr_enabled", True)
    monkeypatch.setattr("app.services.ocr._get_engine", lambda: False)

    store = SqliteStore(str(tmp_path / "index.db"))
    store.init_schema()

    ingest_note(store, {
        "id": "cover-only",
        "url": "https://example.test/note",
        "title": "京都封面图测试",
        "body": "京都咖啡馆收藏",
        "author": "tester",
        "cover": "http://localhost:8000/sampleimg/cover.jpg",
        "images": [],
        "saved_at": "2026-05-12T00:00:00+00:00",
        "published_at": None,
        "source": "test",
    })

    note = store.get_note("cover-only")
    assert note is not None
    assert note["images"] == ["http://localhost:8000/sampleimg/cover.jpg"]
    assert note["image_thumbnails"] == ["/thumbs/65abd1f1d0977c68.jpg"]
    assert (data_dir / "thumb" / "65abd1f1d0977c68.jpg").exists()


def test_inbox_promoted_items_prefer_cached_note_thumbnail(tmp_path):
    store = SqliteStore(str(tmp_path / "index.db"))
    store.init_schema()
    store.upsert_inbox_many([{
        "note_id": "promoted-note",
        "url": "https://www.xiaohongshu.com/explore/promoted-note",
        "title": "封面过期但本地图存在",
        "cover_url": "https://expired.example/cover.jpg",
        "author": "tester",
        "saved_at": "2026-05-12T00:00:00+00:00",
        "discovered_at": "2026-05-12T00:00:00+00:00",
        "raw_meta": {"source": "test"},
    }])
    store.upsert_note({
        "id": "promoted-note",
        "url": "https://www.xiaohongshu.com/explore/promoted-note",
        "title": "封面过期但本地图存在",
        "body": "正文",
        "cover": "https://fresh.example/cover.jpg",
        "images": ["https://fresh.example/cover.jpg"],
        "image_thumbnails": ["/thumbs/local-cover.jpg"],
        "image_ocr_texts": [],
        "trip_purpose": [],
        "image_index_level": 1,
    })
    store.set_inbox_status(["promoted-note"], "promoted")

    rows = store.list_inbox(status="promoted", limit=10)
    _hydrate_note_media(rows, store)

    assert rows[0]["cover_url"] == "https://expired.example/cover.jpg"
    assert rows[0]["note_thumbnail"] == "/thumbs/local-cover.jpg"
    assert rows[0]["note_cover"] == "https://fresh.example/cover.jpg"
