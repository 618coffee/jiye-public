from __future__ import annotations

import os

os.environ["USE_MOCK"] = "true"

from app.routers.cities import poi_geojson  # noqa: E402
from app.services.freshness import refresh_city  # noqa: E402
from app.store.sqlite_store import SqliteStore  # noqa: E402


class _FixedValidator:
    def validate(self, poi):
        return {
            "status": "alive",
            "score": 88,
            "signals": {"source": "test"},
            "poi_update": {
                "lat": 35.0116,
                "lng": 135.7681,
                "address": "Kyoto test address",
                "google_place_id": "places/test",
            },
        }


def test_refresh_city_records_verified_coordinates(tmp_path, monkeypatch):
    store = SqliteStore(str(tmp_path / "geo.db"))
    store.init_schema()
    pid = store.upsert_poi({
        "canonical_name": "茶寮都路里",
        "city": "京都",
        "country": "JP",
        "poi_type": "cafe",
    })
    monkeypatch.setattr(
        "app.services.freshness.get_poi_validator",
        lambda: _FixedValidator(),
    )

    result = refresh_city(store, "京都")
    poi = next(p for p in result["pois"] if p["id"] == pid)

    assert result["refreshed"] == 1
    assert poi["lat"] == 35.0116
    assert poi["lng"] == 135.7681
    assert poi["address"] == "Kyoto test address"
    assert poi["freshness_status"] == "alive"


def test_poi_geojson_exposes_only_mapped_pois(tmp_path):
    store = SqliteStore(str(tmp_path / "geojson.db"))
    store.init_schema()
    mapped_id = store.upsert_poi({
        "canonical_name": "伏见稻荷大社",
        "city": "京都",
        "country": "JP",
        "poi_type": "scenic",
        "lat": 34.9671,
        "lng": 135.7727,
        "address": "Fushimi-ku",
    })
    store.upsert_poi({
        "canonical_name": "未定位地点",
        "city": "京都",
        "country": "JP",
        "poi_type": "place",
    })

    geojson = poi_geojson("京都", store)

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    feature = geojson["features"][0]
    assert feature["geometry"]["coordinates"] == [135.7727, 34.9671]
    assert feature["properties"]["id"] == mapped_id
    assert feature["properties"]["name"] == "伏见稻荷大社"
