"""Pydantic schemas exposed via the API."""
from __future__ import annotations

from typing import Optional, List
from pydantic import BaseModel, Field


class POI(BaseModel):
    id: int
    canonical_name: str
    aliases: List[str] = []
    city: str
    country: str = "CN"
    poi_type: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    freshness_status: Optional[str] = None  # alive | suspect | closed | unknown
    freshness_score: Optional[int] = None
    last_checked_at: Optional[str] = None


class NotePOI(BaseModel):
    poi: POI
    must_try: Optional[str] = None
    price_per_person: Optional[int] = None
    highlight: Optional[str] = None


class Note(BaseModel):
    id: str
    url: Optional[str] = None
    title: str
    body: str
    author: Optional[str] = None
    cover: Optional[str] = None
    images: List[str] = []
    saved_at: Optional[str] = None
    published_at: Optional[str] = None
    primary_city: Optional[str] = None
    primary_country: Optional[str] = None
    trip_purpose: List[str] = []
    ai_summary: Optional[str] = None
    image_index_level: int = 1
    pois: List[NotePOI] = []


class CitySummary(BaseModel):
    city: str
    country: str
    flag: str
    note_count: int
    poi_count: int
    unverified: int
    last_refreshed_at: Optional[str] = None


class CategoryGroup(BaseModel):
    key: str
    label: str
    icon: str
    count: int


class WorkbenchData(BaseModel):
    city: str
    country: str
    flag: str
    notes: List[Note]
    pois: List[POI]
    categories: List[CategoryGroup]


class SearchHit(BaseModel):
    note: Note
    score: float
    matched_chunks: List[str] = []


class SearchResponse(BaseModel):
    query: str
    hits: List[SearchHit]
    explanation: Optional[str] = None


class FreshnessRefreshResponse(BaseModel):
    city: str
    refreshed: int
    skipped: int
    pois: List[POI]
