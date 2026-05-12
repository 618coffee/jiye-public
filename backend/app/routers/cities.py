from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..providers.llm import get_llm_provider
from ..services.freshness import refresh_city as do_refresh
from ..services.ingest import country_flag
from ..store.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/cities", tags=["cities"])


def get_store(request: Request) -> SqliteStore:
    return request.app.state.store


@router.get("")
def list_cities(store: SqliteStore = Depends(get_store)) -> dict:
    rows = store.list_cities()
    return {
        "cities": [
            {
                "city": r["city"],
                "country": r["country"],
                "flag": country_flag(r["country"]),
                "note_count": r["note_count"],
                "poi_count": r["poi_count"],
                "unverified": r["unverified"],
                "last_refreshed_at": r["last_refreshed_at"],
            }
            for r in rows
        ]
    }


@router.get("/{city}/workbench")
def workbench(city: str, store: SqliteStore = Depends(get_store)) -> dict:
    wb = store.workbench(city)
    if not wb["notes"]:
        raise HTTPException(404, f"No notes for city {city}")
    # category groups
    cat_defs = [
        ("food", "美食", "🍱"),
        ("cafe", "咖啡", "☕"),
        ("scenic", "景点", "⛩️"),
        ("shopping", "购物", "🛍️"),
        ("photo", "拍照机位", "📷"),
        ("stay", "住宿", "🏨"),
    ]
    counts: dict[str, int] = {k: 0 for k, _, _ in cat_defs}
    for n in wb["notes"]:
        for p in n.get("trip_purpose", []):
            if p in counts:
                counts[p] += 1
    categories = [
        {"key": k, "label": label, "icon": icon, "count": counts[k]}
        for k, label, icon in cat_defs
    ]
    country = wb["notes"][0].get("primary_country", "CN")

    # ---- LLM clustering with DB cache (TTL by content signature) ----
    sig_src = json.dumps(
        sorted(
            (n["id"], (n.get("ai_summary") or "")[:40],
             tuple(sorted(n.get("trip_purpose") or [])),
             tuple(sorted(((p.get("poi") or {}).get("freshness_status") or "")
                          for p in (n.get("pois") or []))))
            for n in wb["notes"]
        ),
        ensure_ascii=False,
    )
    signature = hashlib.sha1(sig_src.encode()).hexdigest()
    clusters = store.get_cached_clusters(city, signature)
    if clusters is None:
        llm = get_llm_provider()
        clusters = llm.cluster_city(city, wb["notes"])
        store.save_cached_clusters(city, signature, clusters)

    return {
        "city": city,
        "country": country,
        "flag": country_flag(country),
        "notes": wb["notes"],
        "pois": wb["pois"],
        "categories": categories,
        "clusters": clusters,
    }


@router.get("/{city}/pois.geojson")
def poi_geojson(city: str, store: SqliteStore = Depends(get_store)) -> dict:
    wb = store.workbench(city)
    features = []
    for poi in wb["pois"]:
        lat = poi.get("lat")
        lng = poi.get("lng")
        if lat is None or lng is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "id": poi["id"],
                "name": poi["canonical_name"],
                "city": poi.get("city"),
                "country": poi.get("country"),
                "type": poi.get("poi_type"),
                "address": poi.get("address"),
                "freshness_status": poi.get("freshness_status"),
                "freshness_score": poi.get("freshness_score"),
                "last_checked_at": poi.get("last_checked_at"),
            },
        })
    return {
        "type": "FeatureCollection",
        "name": f"{city} POIs",
        "features": features,
    }


@router.post("/{city}/refresh")
def refresh(city: str, store: SqliteStore = Depends(get_store)) -> dict:
    return do_refresh(store, city)
