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
    def update_poi_location(self, poi_id: int, poi: dict[str, Any]) -> None: ...

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

    # ---------- inbox (Phase 2.5: backfill staging) ----------

    @abstractmethod
    def upsert_inbox_many(self, items: list[dict[str, Any]]) -> dict[str, int]: ...

    @abstractmethod
    def list_inbox(
        self, *, status=None, cluster_id=None,
        saved_after=None, saved_before=None, title_query=None,
        limit: int = 500, offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_inbox(self, note_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_inbox_status(self, note_ids: list[str], status: str, *, last_error: str | None = None) -> int: ...

    @abstractmethod
    def set_inbox_cluster(self, note_ids: list[str], cluster_id: str | None) -> int: ...

    @abstractmethod
    def inbox_status_counts(self) -> dict[str, int]: ...

    @abstractmethod
    def replace_clusters(self, clusters: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def list_clusters(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_note_tags(
        self,
        note_id: str,
        tags: list[tuple[str, float]],
        *,
        source: str = "auto",
    ) -> None: ...

    @abstractmethod
    def get_note_tags(self, note_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_note_tags_bulk(
        self, note_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]: ...

    @abstractmethod
    def list_inbox_by_cluster_any(
        self,
        cluster_id: str,
        *,
        status=None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
