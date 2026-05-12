"""On-demand POI freshness refresh per city."""
from __future__ import annotations

from typing import Any

from ..providers import get_poi_validator
from ..store.sqlite_store import SqliteStore


def refresh_city(store: SqliteStore, city: str, *, fresh_window_days: int = 14) -> dict[str, Any]:
    validator = get_poi_validator()
    stale = store.stale_pois_for_city(city, fresh_window_days=fresh_window_days)
    refreshed = 0
    for poi in stale:
        result = validator.validate(poi)
        poi_update = result.get("poi_update") or {}
        if poi_update:
            store.update_poi_location(poi["id"], poi_update)
        store.upsert_freshness(poi["id"], result["status"], result["score"], result["signals"])
        refreshed += 1
    # Re-pull POIs (with new freshness data)
    wb = store.workbench(city)
    return {
        "city": city,
        "refreshed": refreshed,
        "skipped": 0,
        "pois": wb["pois"],
    }
