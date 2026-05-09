"""KnowledgeStore abstract interface (decision 4.5)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class KnowledgeStore(ABC):
    @abstractmethod
    def init_schema(self) -> None: ...

    @abstractmethod
    def upsert_note(self, note: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_poi(self, poi: dict[str, Any]) -> int: ...

    @abstractmethod
    def link_note_poi(self, note_id: str, poi_id: int, link: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_freshness(self, poi_id: int, status: str, score: int, signals: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_cities(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def workbench(self, city: str) -> dict[str, Any]: ...

    @abstractmethod
    def get_note(self, note_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def search(self, query: str, *, limit: int = 20, city: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def stale_pois_for_city(self, city: str, *, fresh_window_days: int = 14) -> list[dict[str, Any]]: ...
