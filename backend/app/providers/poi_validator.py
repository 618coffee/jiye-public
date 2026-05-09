"""POI freshness validator — Amap / Google Places, mock fallback."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any

from ..config import settings


class POIValidator(ABC):
    @abstractmethod
    def validate(self, poi: dict[str, Any]) -> dict[str, Any]:
        """Return {status: alive|suspect|closed|unknown, score: 0-100, signals: {...}}"""


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
        return {"status": status, "score": score, "signals": signals}


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
            return {
                "status": status,
                "score": score,
                "signals": {
                    "source": "amap",
                    "hits": len(pois),
                    "amap_rating": rating,
                    "business_status": biz_status,
                    "address": top.get("address"),
                    "tel": top.get("tel"),
                },
            }
        except Exception as e:
            fallback = MockPOIValidator().validate(poi)
            fallback["signals"]["source"] = f"mock_fallback:{type(e).__name__}"
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
                    "X-Goog-FieldMask": "places.displayName,places.businessStatus,places.rating,places.userRatingCount,places.formattedAddress",
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
            return {
                "status": status,
                "score": score,
                "signals": {
                    "source": "google_places",
                    "hits": len(places),
                    "rating": rating,
                    "user_rating_count": user_count,
                    "business_status": biz,
                    "address": top.get("formattedAddress"),
                },
            }
        except Exception as e:
            fallback = MockPOIValidator().validate(poi)
            fallback["signals"]["source"] = f"mock_fallback:{type(e).__name__}"
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
