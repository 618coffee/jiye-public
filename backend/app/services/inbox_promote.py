"""Promote inbox rows into the main KB.

Pipeline (per note_id):
  1. Read row from ``note_inbox`` (must exist; pulls url, title, cover, author,
     saved_at).
  2. Mark status = ``processing``.
  3. Use :class:`XhsDetailCrawler` to navigate to the URL and harvest body
     text + images + tags.
  4. Build a ``raw`` dict and feed it to :func:`ingest_note`, which runs LLM
     extraction, chunk embedding, POI link, and finally :meth:`upsert_note`.
  5. Mark status = ``promoted``. On failure mark ``failed`` and stash the
     short error message in ``last_error``.

The whole batch is run synchronously — no background queue yet — capped at
``max_per_request`` so the HTTP call doesn't time out. Caller can re-invoke
to continue.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from ..crawler.detail import DetailResult, XhsDetailCrawler
from ..crawler.xhs import CrawlerConfig, CrawlerError
from ..store.sqlite_store import SqliteStore
from .ingest import ingest_note


@dataclass
class PromoteOutcome:
    note_id: str
    status: str  # promoted | failed | skipped
    error: str | None = None
    title: str | None = None


@dataclass
class PromoteRunResult:
    results: list[PromoteOutcome] = field(default_factory=list)
    promoted: int = 0
    failed: int = 0
    skipped: int = 0
    duration_s: float = 0.0


# Hard caps — keep an HTTP request well under typical 60s timeouts and avoid
# triggering xhs anti-bot at scale. UI is expected to chunk larger requests.
DEFAULT_MAX_PER_REQUEST = 20
HARD_MAX_PER_REQUEST = 50


def promote_notes(
    store: SqliteStore,
    note_ids: list[str],
    *,
    max_per_request: int = DEFAULT_MAX_PER_REQUEST,
    cdp_endpoint: str = "http://127.0.0.1:9222",
    per_note_pause_s: float = 2.5,
    per_note_jitter_s: float = 1.2,
    debug_dom: bool = False,
) -> PromoteRunResult:
    """Synchronous wrapper around the async promote pipeline."""
    return asyncio.run(
        _promote_async(
            store,
            note_ids,
            max_per_request=max_per_request,
            cdp_endpoint=cdp_endpoint,
            per_note_pause_s=per_note_pause_s,
            per_note_jitter_s=per_note_jitter_s,
            debug_dom=debug_dom,
        )
    )


async def _promote_async(
    store: SqliteStore,
    note_ids: list[str],
    *,
    max_per_request: int,
    cdp_endpoint: str,
    per_note_pause_s: float,
    per_note_jitter_s: float,
    debug_dom: bool,
) -> PromoteRunResult:
    started = time.monotonic()
    out = PromoteRunResult()
    if not note_ids:
        return out

    cap = max(1, min(max_per_request, HARD_MAX_PER_REQUEST))
    if len(note_ids) > cap:
        # Too-many: process the first ``cap``, mark the rest skipped so the
        # caller can re-submit. We DON'T silently truncate.
        for nid in note_ids[cap:]:
            out.results.append(PromoteOutcome(
                note_id=nid, status="skipped",
                error=f"batch capped at {cap}; resubmit to continue",
            ))
            out.skipped += 1
        note_ids = note_ids[:cap]

    # Resolve (note_id, url) pairs from the inbox table. Skip rows that don't
    # exist or whose status forbids re-processing.
    pending: list[tuple[str, str, dict[str, Any]]] = []
    for nid in note_ids:
        row = store.get_inbox(nid)
        if not row:
            out.results.append(PromoteOutcome(
                note_id=nid, status="failed", error="inbox row not found",
            ))
            out.failed += 1
            continue
        if not row.get("url"):
            out.results.append(PromoteOutcome(
                note_id=nid, status="failed", error="inbox row has no url",
            ))
            out.failed += 1
            continue
        pending.append((nid, row["url"], row))

    if not pending:
        out.duration_s = time.monotonic() - started
        return out

    # Mark all candidates as processing up-front so the UI reflects activity
    # immediately even if a single request takes a while.
    store.set_inbox_status([nid for nid, _, _ in pending], "processing")

    cfg = CrawlerConfig(cdp_endpoint=cdp_endpoint, debug_dom=debug_dom)
    try:
        async with XhsDetailCrawler(cfg) as crawler:
            for i, (nid, url, inbox_row) in enumerate(pending):
                detail: DetailResult = await crawler.fetch_one(nid, url)
                _record(store, out, nid, inbox_row, detail)
                # Pause between notes to be polite to xhs.
                if i + 1 < len(pending):
                    import random
                    await asyncio.sleep(
                        per_note_pause_s + random.random() * per_note_jitter_s
                    )
    except CrawlerError as e:
        # Browser attach failed up-front. Roll the still-processing rows back
        # to pending so the user can retry once Chrome is available again.
        unfinished = [
            o.note_id for nid, _, _ in pending
            for o in out.results if o.note_id == nid and False  # noqa: E501 (placeholder)
        ]
        # Simpler: any pending nid that doesn't yet have an outcome is unfinished.
        done_ids = {o.note_id for o in out.results}
        unfinished = [nid for nid, _, _ in pending if nid not in done_ids]
        if unfinished:
            store.set_inbox_status(unfinished, "pending", last_error=str(e))
        for nid in unfinished:
            out.results.append(PromoteOutcome(
                note_id=nid, status="failed", error=f"browser unavailable: {e}",
            ))
            out.failed += 1

    out.duration_s = time.monotonic() - started
    return out


def _record(
    store: SqliteStore,
    out: PromoteRunResult,
    note_id: str,
    inbox_row: dict[str, Any],
    detail: DetailResult,
) -> None:
    """Apply one detail-fetch outcome: ingest if ok, else mark failed."""
    if not detail.ok:
        err = (detail.error or "unknown detail-page error")[:500]
        store.set_inbox_status([note_id], "failed", last_error=err)
        out.results.append(PromoteOutcome(
            note_id=note_id, status="failed", error=err,
            title=inbox_row.get("title"),
        ))
        out.failed += 1
        return

    # Build the ``raw`` dict that ingest_note() expects. Inbox row supplies
    # the URL + saved_at; the detail crawl supplies title/body/images/etc.
    # Detail-side title beats inbox title (more accurate, longer, cleaner).
    raw = {
        "id": note_id,
        "url": inbox_row.get("url") or detail.url,
        "title": (detail.title or inbox_row.get("title") or "").strip(),
        "body": (detail.body or "").strip(),
        "author": detail.author or inbox_row.get("author"),
        "cover": detail.cover or inbox_row.get("cover_url"),
        "images": detail.images or [],
        "saved_at": inbox_row.get("saved_at"),
        "published_at": detail.published_at,
        "source": "inbox_promote",
    }
    if detail.tags:
        # Stash tags in raw_meta-equivalent; ingest_note doesn't use them
        # directly but they end up on the note dict pre-upsert and may be
        # picked up by future enrichment.
        raw["tags"] = detail.tags

    try:
        ingest_note(store, raw)
        store.set_inbox_status([note_id], "promoted")
        out.results.append(PromoteOutcome(
            note_id=note_id, status="promoted",
            title=raw.get("title") or None,
        ))
        out.promoted += 1
    except Exception as e:
        traceback.print_exc()
        err = f"{type(e).__name__}: {e}"[:500]
        store.set_inbox_status([note_id], "failed", last_error=err)
        out.results.append(PromoteOutcome(
            note_id=note_id, status="failed", error=err,
            title=raw.get("title") or None,
        ))
        out.failed += 1


__all__ = [
    "promote_notes",
    "PromoteRunResult",
    "PromoteOutcome",
    "DEFAULT_MAX_PER_REQUEST",
    "HARD_MAX_PER_REQUEST",
]
