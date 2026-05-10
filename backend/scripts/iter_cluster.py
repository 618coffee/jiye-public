"""Iteration harness for inbox clustering.

Runs cluster_inbox against the live SQLite store, then prints a detailed
diagnostic report:

  - cluster sizes
  - sample of titles per cluster (so we can eyeball whether the label fits)
  - low-confidence rate + a sample of low-conf titles
  - "obvious miss" probes: titles whose theme is unambiguous but ended up
    in low_conf or in a bizarre cluster

Usage:
    cd backend && python scripts/iter_cluster.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.inbox_clustering import cluster_inbox  # noqa: E402
from app.store import SqliteStore  # noqa: E402


# Simple keyword probes: if a title contains any of `keywords`, it should
# *probably* land in a cluster whose name matches `expect_cluster_keywords`
# (case-insensitive). Used only as a directional signal — false positives ok.
PROBES = [
    {
        "name": "travel/旅行",
        "title_kw": [
            "旅行", "出行", "攻略", "行程", "自由行", "机票", "签证",
            "酒店", "民宿", "海岛", "海边", "雪山", "徒步", "户外",
            "露营", "自驾", "公路", "City Walk", "city walk", "城市漫步",
            "出片", "机位", "拍照", "景点",
        ],
        "expect_kw": [
            "旅行", "出行", "户外", "海", "城市", "漫步", "酒店", "自驾",
            "拍照", "出片", "景点", "攻略", "度假",
        ],
    },
    {
        "name": "food/餐厅",
        "title_kw": [
            "餐厅", "美食", "好吃", "探店", "吃货", "私房菜", "日料",
            "火锅", "烧烤", "面馆", "甜品", "蛋糕", "下午茶",
        ],
        "expect_kw": ["餐", "美食", "吃", "甜品", "下午茶", "店"],
    },
    {
        "name": "cafe/咖啡",
        "title_kw": ["咖啡", "拿铁", "美式", "Coffee", "espresso", "手冲"],
        "expect_kw": ["咖啡", "café", "cafe"],
    },
    {
        "name": "fashion/穿搭",
        "title_kw": ["穿搭", "OOTD", "ootd", "搭配", "外套", "牛仔", "连衣裙", "鞋"],
        "expect_kw": ["穿搭", "时尚", "搭配", "outfit"],
    },
    {
        "name": "home/家居",
        "title_kw": ["家居", "装修", "收纳", "卧室", "厨房", "客厅", "宜家", "ikea"],
        "expect_kw": ["家居", "装修", "收纳", "家"],
    },
    {
        "name": "photo-gear/摄影",
        "title_kw": ["相机", "镜头", "Sony", "索尼", "富士", "徕卡", "Leica"],
        "expect_kw": ["摄影", "相机", "器材", "拍照"],
    },
]


def eyeball_assignments(store, clusters, max_per_cluster: int = 8):
    """Return dict cluster_id -> {name, count, titles[]} from the live store."""
    rows = store.list_inbox(status="pending", limit=20_000)
    by_cid: dict[str, list[str]] = defaultdict(list)
    title_by_id: dict[str, str] = {}
    cluster_by_note: dict[str, str] = {}
    for r in rows:
        nid = r["note_id"]
        title_by_id[nid] = r.get("title") or ""
        # primary tag from note_cluster_tags table:
        try:
            tags = store.get_note_tags(nid)  # type: ignore[attr-defined]
        except Exception:
            tags = []
        primary = tags[0]["cluster_id"] if tags else (r.get("cluster_id") or "__none__")
        cluster_by_note[nid] = primary
        by_cid[primary].append(nid)
    out = {}
    for c in clusters:
        cid = c["id"]
        members = by_cid.get(cid, [])
        out[cid] = {
            "name": c["name"],
            "count": c["count"],
            "actual_count": len(members),
            "samples": [title_by_id[m] for m in members[:max_per_cluster]],
        }
    return out, title_by_id, cluster_by_note


def run_probes(title_by_id, cluster_by_note, clusters):
    """For each probe, find titles matching keywords and report where they went."""
    cluster_name = {c["id"]: c["name"] for c in clusters}
    results = []
    for probe in PROBES:
        matched = []
        for nid, t in title_by_id.items():
            if any(kw.lower() in t.lower() for kw in probe["title_kw"]):
                matched.append(nid)
        if not matched:
            continue
        # tally landing clusters
        landing = Counter()
        bad_examples = []
        good = 0
        for nid in matched:
            cid = cluster_by_note.get(nid, "__none__")
            cname = cluster_name.get(cid, cid)
            landing[cname] += 1
            if cid in ("__low_conf__", "__unclustered__", "__none__"):
                if len(bad_examples) < 5:
                    bad_examples.append(title_by_id[nid])
            else:
                # check expect_kw
                if any(kw.lower() in cname.lower() for kw in probe["expect_kw"]):
                    good += 1
                else:
                    if len(bad_examples) < 5:
                        bad_examples.append(f"{title_by_id[nid]}  ->  {cname}")
        results.append({
            "name": probe["name"],
            "matched": len(matched),
            "good_match_pct": round(100 * good / len(matched), 1),
            "top_landings": landing.most_common(5),
            "bad_examples": bad_examples,
        })
    return results


def main():
    os.chdir(Path(__file__).resolve().parent.parent)
    store = SqliteStore(settings.db_path)

    print(f"\n=== iter_cluster: db={settings.db_path} ===")
    rows = store.list_inbox(status="pending", limit=20_000)
    print(f"pending notes: {len(rows)}")

    t0 = time.time()
    result = cluster_inbox(store, status="pending")
    dur = time.time() - t0

    print(f"\n--- run summary ---")
    print(f"  duration:        {dur:.1f}s")
    print(f"  assigned:        {result.assigned}")
    print(f"  skipped:         {result.skipped}")
    print(f"  low-confidence:  {result.low_confidence}  ({100*result.low_confidence/max(1,result.assigned):.1f}%)")
    print(f"  EM moves:        {result.em_moves}")
    print(f"  multi-tagged:    {result.multi_tagged}")
    print(f"  clusters:        {len(result.clusters)}")

    eyeballs, title_by_id, cluster_by_note = eyeball_assignments(store, result.clusters)

    print(f"\n--- clusters (sorted by size) ---")
    sorted_cids = sorted(result.clusters, key=lambda c: -c["count"])
    for c in sorted_cids:
        info = eyeballs.get(c["id"], {})
        print(f"\n  [{c['count']:>4}]  {c['name']}  ({c['id']})")
        print(f"          {c.get('description','')[:80]}")
        for s in info.get("samples", [])[:6]:
            print(f"          · {s[:90]}")

    print(f"\n--- probe results ---")
    probes = run_probes(title_by_id, cluster_by_note, result.clusters)
    for p in probes:
        print(f"\n  {p['name']}: {p['matched']} candidates, {p['good_match_pct']}% landed in expected cluster")
        for cname, n in p["top_landings"]:
            print(f"      -> {n:>3}  {cname}")
        if p["bad_examples"]:
            print(f"      BAD examples:")
            for ex in p["bad_examples"]:
                print(f"        · {ex[:100]}")

    # Final scoring
    score = {
        "low_conf_rate": result.low_confidence / max(1, result.assigned),
        "n_clusters": len(result.clusters),
        "biggest_cluster_pct": (max(c["count"] for c in result.clusters) / max(1, result.assigned)) if result.clusters else 0,
        "probes": [{"name": p["name"], "good_pct": p["good_match_pct"]} for p in probes],
    }
    print(f"\n--- final score ---")
    print(json.dumps(score, ensure_ascii=False, indent=2))

    # Persist for diff across runs
    out_path = Path("scripts/iter_cluster_last.json")
    out_path.write_text(json.dumps({
        "summary": {
            "duration_s": dur,
            "assigned": result.assigned,
            "low_confidence": result.low_confidence,
            "em_moves": result.em_moves,
            "n_clusters": len(result.clusters),
        },
        "clusters": [
            {**c, "samples": eyeballs.get(c["id"], {}).get("samples", [])}
            for c in sorted_cids
        ],
        "probes": probes,
        "score": score,
    }, ensure_ascii=False, indent=2))
    print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
