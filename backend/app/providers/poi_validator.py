"""POI freshness + coordinate validator — Amap / Google Places, mock fallback."""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import Any

from ..config import settings


class POIValidator(ABC):
    @abstractmethod
    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        """Return freshness plus optional ``poi_update`` with lat/lng/address IDs."""


_CITY_CENTERS: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551),
    "成都": (30.5728, 104.0668),
    "重庆": (29.5630, 106.5516),
    "厦门": (24.4798, 118.0894),
    "南京": (32.0603, 118.7969),
    "苏州": (31.2989, 120.5853),
    "东京": (35.6762, 139.6503),
    "京都": (35.0116, 135.7681),
    "大阪": (34.6937, 135.5023),
    "洛杉矶": (34.0522, -118.2437),
    "纽约": (40.7128, -74.0060),
    "旧金山": (37.7749, -122.4194),
    "拉斯维加斯": (36.1699, -115.1398),
    "西雅图": (47.6062, -122.3321),
    "巴黎": (48.8566, 2.3522),
    "伦敦": (51.5072, -0.1276),
    "曼谷": (13.7563, 100.5018),
    "首尔": (37.5665, 126.9780),
    "新加坡": (1.3521, 103.8198),
    "阿姆斯特丹": (52.3676, 4.9041),
    "卡帕多奇亚": (38.6431, 34.8289),
    "冲绳": (26.2124, 127.6809),
}


def _hash_jitter(name: str, *, radius: float = 0.035) -> tuple[float, float]:
    h = int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16)
    lat_jitter = (((h >> 8) % 2001) / 1000.0 - 1.0) * radius
    lng_jitter = (((h >> 20) % 2001) / 1000.0 - 1.0) * radius
    return lat_jitter, lng_jitter


def _mock_location_for(poi: dict[str, Any]) -> dict[str, Any] | None:
    city = (poi.get("city") or "").strip()
    center = _CITY_CENTERS.get(city)
    if center is None:
        return None
    lat_jitter, lng_jitter = _hash_jitter(
        f"{poi.get('canonical_name', '')}:{city}",
    )
    lat, lng = center
    return {
        "lat": round(lat + lat_jitter, 6),
        "lng": round(lng + lng_jitter, 6),
        "address": f"{city} · mock geocode",
    }


def _parse_amap_location(value: str | None) -> tuple[float, float] | None:
    if not value or "," not in value:
        return None
    try:
        lng_s, lat_s = value.split(",", 1)
        return float(lat_s), float(lng_s)
    except (TypeError, ValueError):
        return None


def _out_of_china(lat: float, lng: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    ret += 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    ret += 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    if _out_of_china(lat, lng):
        return lat, lng
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrt_magic * math.cos(radlat) * math.pi)
    mg_lat = lat + dlat
    mg_lng = lng + dlng
    return lat * 2 - mg_lat, lng * 2 - mg_lng


class MockPOIValidator(POIValidator):
    """Deterministic but realistic-looking mock — score derived from hash of name."""

    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        h = int(hashlib.sha1(poi["canonical_name"].encode("utf-8")).hexdigest(), 16)
        score = 35 + (h % 60)  # 35-94
        if score >= 75:
            status = "alive"
        elif score >= 45:
            status = "suspect"
        else:
            status = "closed"
        signals = {
            "amap_rating": round(3.4 + ((h >> 4) % 16) * 0.1, 1),
            "recent_xhs_count": (h >> 8) % 25,
            "neg_keyword_ratio": round(((h >> 12) % 30) / 100.0, 2),
            "source": "mock",
        }
        out: dict[str, Any] = {"status": status, "score": score, "signals": signals}
        location = _mock_location_for(poi)
        if location:
            out["poi_update"] = location
            signals["geocoded"] = True
        return out


class AmapPOIValidator(POIValidator):
    """高德 v3 place/text — search by name + city, infer freshness from rating + business_status."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        import httpx
        name = poi["canonical_name"]
        city = poi.get("city") or ""
        try:
            r = httpx.get(
                "https://restapi.amap.com/v3/place/text",
                params={
                    "key": self.api_key,
                    "keywords": name,
                    "city": city,
                    "citylimit": "true" if city else "false",
                    "extensions": "all",
                    "offset": 5,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            pois = data.get("pois") or []
            if not pois:
                # No hit — likely closed or never existed
                return {
                    "status": "closed",
                    "score": 25,
                    "signals": {"source": "amap", "hits": 0, "reason": "no_match"},
                }
            top = pois[0]
            matched_location = _parse_amap_location(top.get("location"))
            poi_update: dict[str, Any] = {}
            if matched_location:
                lat, lng = _gcj02_to_wgs84(*matched_location)
                poi_update["lat"] = round(lat, 7)
                poi_update["lng"] = round(lng, 7)
            if top.get("address"):
                poi_update["address"] = top.get("address")
            if top.get("id"):
                poi_update["amap_id"] = top.get("id")
            biz_status = (top.get("business_status") or "").strip()  # "1"=营业 "0"=暂停 ""=未知
            rating_raw = top.get("biz_ext", {}).get("rating") or top.get("rating") or ""
            try:
                rating = float(rating_raw) if rating_raw not in ("", "0") else None
            except (TypeError, ValueError):
                rating = None
            # Score model: rating * 18 (0–90) + open bonus
            score = 50
            if rating is not None:
                score = int(min(90, max(20, rating * 18)))
            if biz_status == "0":
                score = min(score, 35)
            if biz_status == "1":
                score += 5
            score = max(0, min(100, score))
            if score >= 75:
                status = "alive"
            elif score >= 45:
                status = "suspect"
            else:
                status = "closed"
            out: dict[str, Any] = {
                "status": status,
                "score": score,
                "signals": {
                    "source": "amap",
                    "hits": len(pois),
                    "matched_name": top.get("name"),
                    "amap_rating": rating,
                    "business_status": biz_status,
                    "address": top.get("address"),
                    "tel": top.get("tel"),
                },
            }
            if poi_update:
                out["poi_update"] = poi_update
            return out
        except Exception as e:
            fallback = MockPOIValidator().validate(poi)
            fallback.pop("poi_update", None)
            fallback["signals"]["source"] = f"mock_fallback:{type(e).__name__}"
            fallback["signals"]["geocoded"] = False
            return fallback


class GooglePlacesValidator(POIValidator):
    """Google Places API (New) — Text Search + business_status."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        import httpx
        name = poi["canonical_name"]
        city = poi.get("city") or ""
        query = f"{name} {city}".strip()
        try:
            r = httpx.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "places.id,places.displayName,places.businessStatus,places.rating,places.userRatingCount,places.formattedAddress,places.location",
                },
                json={"textQuery": query, "maxResultCount": 5},
                timeout=15,
            )
            r.raise_for_status()
            places = r.json().get("places") or []
            if not places:
                return {
                    "status": "closed",
                    "score": 25,
                    "signals": {"source": "google_places", "hits": 0, "reason": "no_match"},
                }
            top = places[0]
            loc = top.get("location") or {}
            poi_update: dict[str, Any] = {}
            if loc.get("latitude") is not None and loc.get("longitude") is not None:
                poi_update["lat"] = float(loc["latitude"])
                poi_update["lng"] = float(loc["longitude"])
            if top.get("formattedAddress"):
                poi_update["address"] = top.get("formattedAddress")
            if top.get("id"):
                poi_update["google_place_id"] = top.get("id")
            biz = top.get("businessStatus", "OPERATIONAL")
            rating = top.get("rating")
            user_count = top.get("userRatingCount", 0)
            score = 50
            if rating is not None:
                score = int(min(95, max(20, rating * 18 + min(10, user_count / 100))))
            if biz == "CLOSED_PERMANENTLY":
                score = 15
            elif biz == "CLOSED_TEMPORARILY":
                score = min(score, 40)
            score = max(0, min(100, score))
            if score >= 75:
                status = "alive"
            elif score >= 45:
                status = "suspect"
            else:
                status = "closed"
            display_name = top.get("displayName") or {}
            out: dict[str, Any] = {
                "status": status,
                "score": score,
                "signals": {
                    "source": "google_places",
                    "hits": len(places),
                    "matched_name": display_name.get("text"),
                    "rating": rating,
                    "user_rating_count": user_count,
                    "business_status": biz,
                    "address": top.get("formattedAddress"),
                },
            }
            if poi_update:
                out["poi_update"] = poi_update
            return out
        except Exception as e:
            fallback = MockPOIValidator().validate(poi)
            fallback.pop("poi_update", None)
            fallback["signals"]["source"] = f"mock_fallback:{type(e).__name__}"
            fallback["signals"]["geocoded"] = False
            return fallback


# Domestic CN cities → Amap; everything else → Google Places.
_CN_CITIES = {
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "厦门", "南京",
    "苏州", "武汉", "西安", "天津", "青岛", "大连", "沈阳", "长沙", "郑州",
    "昆明", "三亚", "海口", "济南", "无锡", "宁波", "福州", "合肥",
}


class RoutingPOIValidator(POIValidator):
    """Choose Amap (CN) or Google Places (overseas) per POI."""

    def __init__(self, amap: POIValidator | None, google: POIValidator | None):
        self.amap = amap
        self.google = google
        self.fallback = MockPOIValidator()

    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        city = (poi.get("city") or "").strip()
        country = (poi.get("country") or "").strip().upper()
        is_cn = country == "CN" or city in _CN_CITIES
        primary = self.amap if is_cn else self.google
        if primary is None:
            primary = self.amap or self.google
        if primary is None:
            return self.fallback.validate(poi)
        return primary.validate(poi)


_singleton: POIValidator | None = None


def get_poi_validator() -> POIValidator:
    global _singleton
    if _singleton is not None:
        return _singleton
    if settings.use_mock == "true":
        _singleton = MockPOIValidator()
        return _singleton
    amap = AmapPOIValidator(settings.amap_api_key) if settings.amap_api_key else None
    google = GooglePlacesValidator(settings.google_places_api_key) if settings.google_places_api_key else None
    if amap is None and google is None:
        _singleton = MockPOIValidator()
    else:
        _singleton = RoutingPOIValidator(amap, google)
    return _singleton
