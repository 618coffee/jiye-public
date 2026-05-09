"""Tests for two-phase inbox clustering.

Forces the mock LLM + mock embedding providers via env vars so the test runs
fully offline and deterministically. The clustering algorithm itself is
identical to production — only the underlying providers differ.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

# Force mock providers BEFORE importing app modules.
os.environ["USE_MOCK"] = "true"

from app.providers import embedding as _embed_mod  # noqa: E402
from app.providers import llm as _llm_mod  # noqa: E402
from app.services.inbox_clustering import cluster_inbox  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


def _reset_provider_singletons():
    _embed_mod._singleton = None
    _llm_mod._singleton = None


SAMPLE_TITLES = [
    # 美食 / food
    "京都祇园抹茶圣代必吃",
    "东京拉面三天吃了七家",
    "大阪心斋桥鳗鱼饭推荐",
    "上海外滩米其林餐厅清单",
    "成都串串攻略 9 家本地人爱去",
    "重庆小面早餐打卡",
    "广州早茶必点 12 道",
    # 咖啡 / cafe
    "上海咖啡地图 30 家精品店",
    "东京 Arabica 咖啡拉花",
    "杭州独立咖啡馆探店",
    "京都 Kurasu 咖啡",
    # 景点 / scenic
    "京都伏见稻荷大社清晨拍照",
    "东京浅草寺夜景",
    "成都大熊猫基地半日游",
    "杭州西湖断桥日落",
    # 拍照 / photo
    "上海武康路出片机位",
    "东京涉谷十字路口全景拍照",
    # other
    "iPhone 17 Pro 上手体验",
    "MacBook Air M5 评测",
    "本周读完三本书的笔记",
]


def _seed_inbox(store: SqliteStore, titles: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, t in enumerate(titles):
        rows.append({
            "note_id": f"n{i:03d}",
            "url": f"https://www.xiaohongshu.com/explore/n{i:03d}",
            "title": t,
            "cover_url": None,
            "author": "tester",
            "saved_at": now,
            "discovered_at": now,
            "raw_meta": {"source": "test"},
        })
    store.upsert_inbox_many(rows)


def test_cluster_inbox_assigns_every_title_to_some_cluster(tmp_path):
    _reset_provider_singletons()
    db = tmp_path / "inbox_cluster_test.db"
    store = SqliteStore(str(db))
    store.init_schema()
    _seed_inbox(store, SAMPLE_TITLES)

    res = cluster_inbox(
        store, status="pending", target_clusters=8, sample_size=20,
    )

    # Every non-empty title should be assigned (mock embedding is deterministic).
    assert res.assigned == len(SAMPLE_TITLES), (
        f"expected all {len(SAMPLE_TITLES)} titles assigned, got {res.assigned}"
    )
    assert res.skipped == 0
    assert res.duration_s >= 0

    # Clusters must persist & sum to total.
    clusters = store.list_clusters()
    assert clusters, "no clusters were persisted"
    total = sum(c["count"] for c in clusters)
    assert total == len(SAMPLE_TITLES), (
        f"cluster counts sum to {total} != total {len(SAMPLE_TITLES)}"
    )

    # Spot-check: every inbox row now has a cluster_id.
    rows = store.list_inbox(status="pending", limit=100)
    assert all(r.get("cluster_id") for r in rows), "some inbox rows missing cluster_id"


def test_cluster_inbox_handles_empty_inbox(tmp_path):
    _reset_provider_singletons()
    db = tmp_path / "empty_inbox.db"
    store = SqliteStore(str(db))
    store.init_schema()

    res = cluster_inbox(store, status="pending")
    assert res.clusters == []
    assert res.assigned == 0
    assert res.skipped == 0


def test_cluster_inbox_only_unassigned_skips_already_clustered(tmp_path):
    _reset_provider_singletons()
    db = tmp_path / "partial_inbox.db"
    store = SqliteStore(str(db))
    store.init_schema()
    _seed_inbox(store, SAMPLE_TITLES)

    # First pass clusters everything.
    cluster_inbox(store, status="pending", target_clusters=6, sample_size=20)
    rows = store.list_inbox(status="pending", limit=100)
    assert all(r.get("cluster_id") for r in rows)

    # Add one new note, then re-run with only_unassigned=True.
    new_title = "巴黎咖啡馆推荐"
    _seed_inbox(store, [new_title])
    # Note: _seed_inbox uses index 0 again -> 'n000' which already exists.
    # That row's status stays pending (no overwrite of status), and the new
    # title gets persisted but cluster_id is preserved by upsert. Verify by
    # explicitly inserting a brand-new id:
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_inbox_many([{
        "note_id": "fresh-001",
        "url": "https://www.xiaohongshu.com/explore/fresh-001",
        "title": "新加坡夜游必打卡 5 个景点",
        "cover_url": None,
        "author": "tester",
        "saved_at": now,
        "discovered_at": now,
        "raw_meta": {"source": "test"},
    }])

    res = cluster_inbox(
        store, status="pending", target_clusters=6,
        sample_size=20, only_unassigned=True,
    )
    # Should only consider the 1 new unassigned row (others all already had
    # cluster_id from the first run). assigned should be exactly 1.
    assert res.assigned == 1, f"expected 1, got {res.assigned}"
