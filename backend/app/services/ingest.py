"""Ingestion: take a raw note dict, run AI pipeline, persist."""
from __future__ import annotations

from typing import Any

from ..providers import get_embedding_provider, get_llm_provider
from ..providers.llm import _CITY_HINTS
from ..store.base import KnowledgeStore


COUNTRY_FLAGS = {
    "CN": "🇨🇳", "JP": "🇯🇵", "US": "🇺🇸", "FR": "🇫🇷",
    "TH": "🇹🇭", "KR": "🇰🇷", "GB": "🇬🇧", "IT": "🇮🇹",
    "DE": "🇩🇪", "ES": "🇪🇸", "SG": "🇸🇬", "MY": "🇲🇾",
    "VN": "🇻🇳", "ID": "🇮🇩", "AU": "🇦🇺",
}


def country_flag(code: str | None) -> str:
    return COUNTRY_FLAGS.get((code or "CN").upper(), "🏳️")


def ingest_note(store: KnowledgeStore, raw: dict[str, Any]) -> str:
    """raw: {id, url, title, body, author, cover, images, saved_at, published_at}

    Pipeline:
      1. LLM extract (city, purposes, pois, summary)
      2. Build chunks (title / body / poi_card)
      3. Embed chunks
      4. Persist note + chunks + pois
    """
    llm = get_llm_provider()
    embed = get_embedding_provider()

    full_text = "\n".join(filter(None, [raw.get("title"), raw.get("body")]))
    extracted = llm.extract_note(full_text)

    # Safety net: if LLM didn't pick a city, scan the text for a known alias.
    # Also normalise English aliases (e.g. "Los Angeles" -> "洛杉矶").
    raw_city = (extracted.get("primary_city") or "").strip()
    raw_country = (extracted.get("primary_country") or "").strip().upper()
    if not raw_city or raw_city.lower() in {"null", "none", "unknown"}:
        for alias, (cn_city, cn_country) in _CITY_HINTS.items():
            if alias in full_text:
                raw_city, raw_country = cn_city, cn_country
                break
    else:
        for alias, (cn_city, cn_country) in _CITY_HINTS.items():
            if alias.lower() == raw_city.lower():
                raw_city, raw_country = cn_city, cn_country
                break
    extracted["primary_city"] = raw_city or None
    extracted["primary_country"] = raw_country or "CN"

    chunks: list[dict[str, Any]] = []
    if raw.get("title"):
        chunks.append({"chunk_type": "title", "text": raw["title"]})
    if raw.get("body"):
        for i, para in enumerate(_split_body(raw["body"])):
            chunks.append({"chunk_type": "body", "text": para})

    poi_cards: list[str] = []
    persist_pois: list[dict[str, Any]] = []
    for p in extracted.get("pois", []) or []:
        name = p.get("name")
        if not name:
            continue
        # poi_card chunk for retrieval
        bits = [name]
        if p.get("type"):
            bits.append(p["type"])
        if extracted.get("primary_city"):
            bits.append(extracted["primary_city"])
        if p.get("must_try"):
            bits.append(f"必点 {p['must_try']}")
        if p.get("price_per_person"):
            bits.append(f"人均 {p['price_per_person']}")
        if p.get("highlight"):
            bits.append(p["highlight"])
        poi_card = " · ".join(bits)
        chunks.append({"chunk_type": "poi_card", "text": poi_card})
        poi_cards.append(poi_card)
        persist_pois.append({
            "canonical_name": name,
            "city": extracted.get("primary_city"),
            "country": extracted.get("primary_country", "CN"),
            "poi_type": p.get("type"),
            "must_try": p.get("must_try"),
            "price_per_person": p.get("price_per_person"),
            "highlight": p.get("highlight"),
        })

    # Embed
    if chunks:
        vectors = embed.embed([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors):
            c["embedding"] = v

    note = {
        **raw,
        "primary_city": extracted.get("primary_city"),
        "primary_country": extracted.get("primary_country", "CN"),
        "city_confidence": extracted.get("city_confidence", 0.9),
        "trip_purpose": extracted.get("trip_purpose", []),
        "ai_summary": extracted.get("summary"),
        "image_index_level": raw.get("image_index_level", 1),
        "chunks": chunks,
    }
    store.upsert_note(note)

    # POIs
    for pp in persist_pois:
        pid = store.upsert_poi({
            "canonical_name": pp["canonical_name"],
            "city": pp["city"],
            "country": pp["country"],
            "poi_type": pp["poi_type"],
        })
        store.link_note_poi(raw["id"], pid, {
            "must_try": pp.get("must_try"),
            "price_per_person": pp.get("price_per_person"),
            "highlight": pp.get("highlight"),
        })
    return raw["id"]


def _split_body(body: str, max_len: int = 280) -> list[str]:
    out: list[str] = []
    cur = ""
    for line in body.splitlines():
        line = line.strip()
        if not line:
            if cur:
                out.append(cur)
                cur = ""
            continue
        if len(cur) + len(line) + 1 > max_len and cur:
            out.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out
