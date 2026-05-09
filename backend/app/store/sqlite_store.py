"""KnowledgeStore: SQLite implementation (Phase 1 self-host backend).

Uses pure stdlib sqlite3. sqlite-vec is optional — if not present we fall back to
in-memory cosine over the embeddings column (fine for the local-demo scale).
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .base import KnowledgeStore


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT,
  url           TEXT,
  title         TEXT,
  body          TEXT,
  author        TEXT,
  cover         TEXT,
  published_at  TEXT,
  saved_at      TEXT,
  image_paths   TEXT,
  image_thumbnails TEXT,
  image_ocr_texts TEXT,
  primary_city  TEXT,
  primary_country TEXT,
  city_confidence REAL,
  trip_purpose  TEXT,
  text_index_status TEXT DEFAULT 'done',
  image_index_level INT DEFAULT 1,
  ai_summary    TEXT
);

CREATE INDEX IF NOT EXISTS idx_notes_city ON notes(primary_city);
CREATE INDEX IF NOT EXISTS idx_notes_saved_at ON notes(saved_at DESC);

CREATE TABLE IF NOT EXISTS pois (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     TEXT,
  canonical_name TEXT NOT NULL,
  aliases       TEXT,
  city          TEXT,
  country       TEXT DEFAULT 'CN',
  poi_type      TEXT,
  lat REAL, lng REAL,
  amap_id       TEXT,
  google_place_id TEXT,
  address       TEXT,
  UNIQUE(canonical_name, city)
);
CREATE INDEX IF NOT EXISTS idx_pois_city ON pois(city);

CREATE TABLE IF NOT EXISTS note_poi (
  note_id       TEXT,
  poi_id        INTEGER,
  must_try      TEXT,
  price_per_person INTEGER,
  highlight     TEXT,
  sentiment     REAL,
  PRIMARY KEY (note_id, poi_id)
);

CREATE TABLE IF NOT EXISTS poi_freshness (
  poi_id        INTEGER PRIMARY KEY,
  status        TEXT,
  score         INTEGER,
  signals       TEXT,
  last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS note_chunks (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id   TEXT,
  chunk_type TEXT,
  text      TEXT,
  image_idx INTEGER,
  embedding TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_note ON note_chunks(note_id);

CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_fts USING fts5(
  text, chunk_id UNINDEXED, note_id UNINDEXED, tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS city_workbench_cache (
  city          TEXT PRIMARY KEY,
  payload       TEXT NOT NULL,
  notes_signature TEXT,
  created_at    TEXT NOT NULL
);
"""


def _vec(s: str | None) -> list[float] | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SqliteStore(KnowledgeStore):
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    # ---------- schema ----------

    def init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Backwards-compatible additive migrations
            cols = {r[1] for r in c.execute("PRAGMA table_info(notes)").fetchall()}
            for col in ("image_thumbnails", "image_ocr_texts"):
                if col not in cols:
                    c.execute(f"ALTER TABLE notes ADD COLUMN {col} TEXT")

    # ---------- writes ----------

    def upsert_note(self, note: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO notes (id, url, title, body, author, cover, published_at,
                  saved_at, image_paths, image_thumbnails, image_ocr_texts,
                  primary_city, primary_country, city_confidence,
                  trip_purpose, image_index_level, ai_summary)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title, body=excluded.body, ai_summary=excluded.ai_summary,
                  image_thumbnails=excluded.image_thumbnails, image_ocr_texts=excluded.image_ocr_texts,
                  primary_city=excluded.primary_city, primary_country=excluded.primary_country,
                  trip_purpose=excluded.trip_purpose, image_index_level=excluded.image_index_level
                """,
                (
                    note["id"], note.get("url"), note.get("title", ""), note.get("body", ""),
                    note.get("author"), note.get("cover"), note.get("published_at"),
                    note.get("saved_at"), json.dumps(note.get("images", []), ensure_ascii=False),
                    json.dumps(note.get("image_thumbnails", []), ensure_ascii=False),
                    json.dumps(note.get("image_ocr_texts", []), ensure_ascii=False),
                    note.get("primary_city"), note.get("primary_country", "CN"),
                    note.get("city_confidence", 1.0),
                    json.dumps(note.get("trip_purpose", []), ensure_ascii=False),
                    note.get("image_index_level", 1),
                    note.get("ai_summary"),
                ),
            )
            # Replace chunks
            c.execute("DELETE FROM note_chunks WHERE note_id = ?", (note["id"],))
            c.execute("DELETE FROM note_chunks_fts WHERE note_id = ?", (note["id"],))
            for ch in note.get("chunks", []):
                cur = c.execute(
                    "INSERT INTO note_chunks (note_id, chunk_type, text, image_idx, embedding) VALUES (?,?,?,?,?)",
                    (note["id"], ch.get("chunk_type", "body"), ch["text"],
                     ch.get("image_idx"),
                     json.dumps(ch["embedding"]) if ch.get("embedding") else None),
                )
                chunk_id = cur.lastrowid
                c.execute(
                    "INSERT INTO note_chunks_fts (rowid, text, chunk_id, note_id) VALUES (?,?,?,?)",
                    (chunk_id, ch["text"], chunk_id, note["id"]),
                )

    def upsert_poi(self, poi: dict[str, Any]) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM pois WHERE canonical_name = ? AND city = ?",
                (poi["canonical_name"], poi.get("city")),
            ).fetchone()
            if row:
                pid = row["id"]
                c.execute(
                    """UPDATE pois SET aliases=?, country=?, poi_type=?, lat=?, lng=?,
                       amap_id=?, google_place_id=?, address=? WHERE id=?""",
                    (json.dumps(poi.get("aliases", []), ensure_ascii=False),
                     poi.get("country", "CN"), poi.get("poi_type"),
                     poi.get("lat"), poi.get("lng"),
                     poi.get("amap_id"), poi.get("google_place_id"),
                     poi.get("address"), pid),
                )
                return pid
            cur = c.execute(
                """INSERT INTO pois (canonical_name, aliases, city, country, poi_type, lat, lng,
                   amap_id, google_place_id, address) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (poi["canonical_name"], json.dumps(poi.get("aliases", []), ensure_ascii=False),
                 poi.get("city"), poi.get("country", "CN"), poi.get("poi_type"),
                 poi.get("lat"), poi.get("lng"),
                 poi.get("amap_id"), poi.get("google_place_id"), poi.get("address")),
            )
            return cur.lastrowid

    def link_note_poi(self, note_id: str, poi_id: int, link: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO note_poi (note_id, poi_id, must_try, price_per_person, highlight, sentiment)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(note_id, poi_id) DO UPDATE SET
                  must_try=excluded.must_try, price_per_person=excluded.price_per_person,
                  highlight=excluded.highlight, sentiment=excluded.sentiment""",
                (note_id, poi_id, link.get("must_try"), link.get("price_per_person"),
                 link.get("highlight"), link.get("sentiment")),
            )

    def upsert_freshness(self, poi_id: int, status: str, score: int, signals: dict[str, Any]) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                """INSERT INTO poi_freshness (poi_id, status, score, signals, last_checked_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(poi_id) DO UPDATE SET
                  status=excluded.status, score=excluded.score, signals=excluded.signals,
                  last_checked_at=excluded.last_checked_at""",
                (poi_id, status, score, json.dumps(signals, ensure_ascii=False), now),
            )

    # ---------- reads ----------

    @staticmethod
    def _note_row_to_dict(c: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["images"] = json.loads(d.pop("image_paths") or "[]")
        d["image_thumbnails"] = json.loads(d.pop("image_thumbnails", None) or "[]")
        d["image_ocr_texts"] = json.loads(d.pop("image_ocr_texts", None) or "[]")
        d["trip_purpose"] = json.loads(d.get("trip_purpose") or "[]")
        # Attach pois
        pois = c.execute(
            """SELECT p.*, np.must_try, np.price_per_person, np.highlight,
                      f.status AS freshness_status, f.score AS freshness_score, f.last_checked_at
               FROM note_poi np
               JOIN pois p ON p.id = np.poi_id
               LEFT JOIN poi_freshness f ON f.poi_id = p.id
               WHERE np.note_id = ?""",
            (d["id"],),
        ).fetchall()
        d["pois"] = []
        for pr in pois:
            pd = dict(pr)
            pd["aliases"] = json.loads(pd.get("aliases") or "[]")
            d["pois"].append({
                "poi": {
                    "id": pd["id"],
                    "canonical_name": pd["canonical_name"],
                    "aliases": pd["aliases"],
                    "city": pd["city"],
                    "country": pd["country"],
                    "poi_type": pd["poi_type"],
                    "address": pd.get("address"),
                    "lat": pd.get("lat"),
                    "lng": pd.get("lng"),
                    "freshness_status": pd.get("freshness_status"),
                    "freshness_score": pd.get("freshness_score"),
                    "last_checked_at": pd.get("last_checked_at"),
                },
                "must_try": pd.get("must_try"),
                "price_per_person": pd.get("price_per_person"),
                "highlight": pd.get("highlight"),
            })
        return d

    def list_cities(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT primary_city AS city, primary_country AS country,
                          COUNT(*) AS note_count
                   FROM notes
                   WHERE primary_city IS NOT NULL AND primary_city != ''
                   GROUP BY primary_city, primary_country
                   ORDER BY note_count DESC""",
            ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                pcount = c.execute(
                    "SELECT COUNT(DISTINCT id) AS n FROM pois WHERE city = ?",
                    (d["city"],),
                ).fetchone()["n"]
                unv = c.execute(
                    """SELECT COUNT(*) AS n FROM pois p
                       LEFT JOIN poi_freshness f ON f.poi_id = p.id
                       WHERE p.city = ?
                         AND (f.last_checked_at IS NULL OR
                              julianday('now') - julianday(f.last_checked_at) > 14)""",
                    (d["city"],),
                ).fetchone()["n"]
                last = c.execute(
                    """SELECT MAX(f.last_checked_at) AS t FROM poi_freshness f
                       JOIN pois p ON p.id = f.poi_id WHERE p.city = ?""",
                    (d["city"],),
                ).fetchone()["t"]
                d["poi_count"] = pcount
                d["unverified"] = unv
                d["last_refreshed_at"] = last
                out.append(d)
            return out

    def workbench(self, city: str) -> dict[str, Any]:
        with self._conn() as c:
            note_rows = c.execute(
                "SELECT * FROM notes WHERE primary_city = ? ORDER BY saved_at DESC",
                (city,),
            ).fetchall()
            notes = [self._note_row_to_dict(c, r) for r in note_rows]
            poi_rows = c.execute(
                """SELECT p.*, f.status AS freshness_status, f.score AS freshness_score,
                          f.last_checked_at
                   FROM pois p
                   LEFT JOIN poi_freshness f ON f.poi_id = p.id
                   WHERE p.city = ?""",
                (city,),
            ).fetchall()
            pois = []
            for r in poi_rows:
                d = dict(r)
                d["aliases"] = json.loads(d.get("aliases") or "[]")
                pois.append(d)
            return {"notes": notes, "pois": pois}

    def get_cached_clusters(self, city: str, signature: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT payload, notes_signature FROM city_workbench_cache WHERE city = ?",
                (city,),
            ).fetchone()
            if not row or row["notes_signature"] != signature:
                return None
            try:
                return json.loads(row["payload"])
            except Exception:
                return None

    def save_cached_clusters(self, city: str, signature: str, payload: dict[str, Any]) -> None:
        from datetime import datetime, timezone
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO city_workbench_cache (city, payload, notes_signature, created_at) VALUES (?, ?, ?, ?)",
                (city, json.dumps(payload, ensure_ascii=False), signature, datetime.now(timezone.utc).isoformat()),
            )

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if not row:
                return None
            return self._note_row_to_dict(c, row)

    def search(self, query: str, *, limit: int = 20, city: str | None = None) -> list[dict[str, Any]]:
        # Hybrid: FTS5 + (optional) vector cosine merge.
        # Vector embedding is provided by caller via providers; here we only do FTS.
        # Vector blending happens in the search service layer.
        with self._conn() as c:
            sql_filters = ""
            args: list[Any] = []
            if city:
                sql_filters += " AND notes.primary_city = ?"
                args.append(city)
            try:
                escaped = self._fts_escape(query)
                rows = c.execute(
                    f"""SELECT note_chunks_fts.note_id AS note_id,
                              note_chunks_fts.chunk_id AS chunk_id,
                              snippet(note_chunks_fts, 0, '<em>', '</em>', '…', 16) AS snippet,
                              bm25(note_chunks_fts) AS rank
                       FROM note_chunks_fts
                       JOIN notes ON notes.id = note_chunks_fts.note_id
                       WHERE note_chunks_fts MATCH ?{sql_filters}
                       ORDER BY rank LIMIT ?""",
                    (escaped, *args, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            results: dict[str, dict[str, Any]] = {}
            for r in rows:
                nid = r["note_id"]
                if nid not in results:
                    results[nid] = {"note_id": nid, "score": 0.0, "matched_chunks": []}
                # bm25 is "lower=better"; convert to score
                s = 1.0 / (1.0 + max(r["rank"], 0.0))
                results[nid]["score"] += s
                results[nid]["matched_chunks"].append(r["snippet"])
            return list(results.values())

    @staticmethod
    def _fts_escape(query: str) -> str:
        # Quote each whitespace-separated term as a phrase to be safe with CJK / punctuation.
        parts = [p for p in query.replace('"', "").split() if p]
        if not parts:
            # Fallback to fuzzy single-token query
            return f'"{query.strip()}"' if query.strip() else '""'
        return " OR ".join(f'"{p}"' for p in parts)

    def chunks_with_embeddings(self, note_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            if note_ids:
                ids = list(note_ids)
                qmarks = ",".join("?" * len(ids))
                rows = c.execute(
                    f"SELECT * FROM note_chunks WHERE note_id IN ({qmarks}) AND embedding IS NOT NULL",
                    ids,
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM note_chunks WHERE embedding IS NOT NULL"
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["embedding"] = _vec(d["embedding"])
                out.append(d)
            return out

    def vector_search(self, query_emb: list[float], *, limit: int = 20, city: str | None = None) -> list[dict[str, Any]]:
        chunks = self.chunks_with_embeddings()
        scored: dict[str, dict[str, Any]] = {}
        all_sims: list[float] = []
        with self._conn() as c:
            if city:
                rows = c.execute(
                    "SELECT id, primary_city FROM notes WHERE primary_city = ?",
                    (city,),
                ).fetchall()
                allowed = {r["id"] for r in rows}
            else:
                allowed = None
        for ch in chunks:
            if allowed is not None and ch["note_id"] not in allowed:
                continue
            sim = _cosine(query_emb, ch["embedding"])
            if sim <= 0:
                continue
            all_sims.append(sim)
            nid = ch["note_id"]
            if nid not in scored or sim > scored[nid]["score"]:
                scored[nid] = {"note_id": nid, "score": sim, "matched_chunks": [ch["text"][:120]]}
        # Per-query baseline. Multilingual e5 has a high cosine floor (~0.8 even
        # for unrelated CN/EN text), so absolute thresholds don't transfer
        # between queries. Instead we keep only chunks whose similarity is at
        # least ``mean + Z * std`` standard deviations above the corpus-wide
        # mean for *this* query — i.e. results that genuinely stand out.
        if all_sims:
            n = len(all_sims)
            mean = sum(all_sims) / n
            var = sum((s - mean) ** 2 for s in all_sims) / max(n - 1, 1)
            std = math.sqrt(var) if var > 0 else 0.0
            z_cut = mean + 1.5 * std
            for nid, h in scored.items():
                # store a normalised relevance score = (sim - mean) / std,
                # callers can use it for relative ranking.
                h["raw_cos"] = h["score"]
                h["z"] = (h["score"] - mean) / std if std > 0 else 0.0
                h["baseline_mean"] = mean
                h["baseline_std"] = std
            kept = [h for h in scored.values() if h["score"] >= z_cut]
        else:
            kept = list(scored.values())
        ordered = sorted(kept, key=lambda x: x["score"], reverse=True)
        return ordered[:limit]

    def stale_pois_for_city(self, city: str, *, fresh_window_days: int = 14) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT p.* FROM pois p
                   LEFT JOIN poi_freshness f ON f.poi_id = p.id
                   WHERE p.city = ?
                     AND (f.last_checked_at IS NULL
                          OR julianday('now') - julianday(f.last_checked_at) > ?)""",
                (city, fresh_window_days),
            ).fetchall()
            return [dict(r) for r in rows]
