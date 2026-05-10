"""Enrich inbox notes with body + tags by clicking cards in the logged-in xhs UI.

This is the fallback path when the cookieless /explore HTML endpoint is
throttled (300013). It attaches to the user's real Chrome over CDP, opens the
collection page, reads the currently loaded feed order, then clicks each note's
token-bearing anchor so the SPA modal renders full title/body/tags.

Usage:
    cd backend && python scripts/enrich_inbox_spa.py [--limit N] [--retry-failed]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.crawler.detail import XhsDetailCrawler  # noqa: E402
from app.crawler.xhs import CrawlerConfig  # noqa: E402


FEED_ORDER_JS = r"""
() => {
  const seen = new Set();
  const out = [];
  const anchors = Array.from(document.querySelectorAll('a[href*="xsec_token="]'));
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/([0-9a-f]{18,32})\?xsec_token=/i);
    if (!m) continue;
    const noteId = m[1];
    if (seen.has(noteId)) continue;
    seen.add(noteId);
        out.push({ note_id: noteId, href });
  }
  return out;
}
"""


def _load_rows(conn: sqlite3.Connection, retry_failed: bool, limit: int | None) -> dict[str, sqlite3.Row]:
    where = "status='pending'"
    if retry_failed:
        where += " AND raw_meta LIKE '%\"detail\"%' AND raw_meta LIKE '%\"ok\": false%'"
    else:
        where += " AND (raw_meta NOT LIKE '%\"detail\"%' OR raw_meta IS NULL)"
    sql = f"SELECT note_id, title, url, raw_meta FROM note_inbox WHERE {where}"
    if limit:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    return {r["note_id"]: r for r in rows}


def _commit(conn: sqlite3.Connection, row: sqlite3.Row, detail: dict) -> None:
    meta = json.loads(row["raw_meta"] or "{}")
    old = meta.get("detail") or {}
    if old.get("ok") and not detail.get("ok"):
        return
    meta["detail"] = detail
    conn.execute(
        "UPDATE note_inbox SET raw_meta=? WHERE note_id=?",
        (json.dumps(meta, ensure_ascii=False), row["note_id"]),
    )
    conn.commit()


async def main_async(limit: int | None, retry_failed: bool, pause: float, debug: bool):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    pending = _load_rows(conn, retry_failed=retry_failed, limit=None)
    cap = limit or len(pending)
    print(f"to enrich: {len(pending)}" + (f" (cap={cap})" if limit else ""))
    if not pending:
        return

    t0 = time.time()
    ok = fail = skipped_not_loaded = 0
    async with XhsDetailCrawler(CrawlerConfig(debug_dom=debug)) as crawler:
        # Use the first row's page_url as a hint; open_collection will reuse an
        # existing collection tab whenever possible.
        first_meta = json.loads(next(iter(pending.values()))["raw_meta"] or "{}")
        await crawler.open_collection(first_meta.get("page_url"))
        assert crawler._page is not None

        # xhs keeps the masonry feed in SPA state after modal clicks. Start
        # enrichment from a freshly rendered collection feed, matching the
        # successful scan_inbox path that can load the full 900+ item list.
        try:
            await crawler._page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
            await crawler._page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(1.2)
            await crawler._ensure_collection_tab()
        except Exception as exc:
            if debug:
                print(f"  reload collection failed; continuing: {exc}", flush=True)

        # Start at the top and scroll the feed to build newest-first order.
        await crawler._page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1.0)
        seen_feed: set[str] = set()
        processed: set[str] = set()
        idle = 0
        for scroll_idx in range(900):
            current = await crawler._page.evaluate(FEED_ORDER_JS)
            before = len(seen_feed)
            current_target: tuple[str, str] | None = None
            for item in current:
                if isinstance(item, str):
                    nid = item
                    href = ""
                else:
                    nid = item.get("note_id")
                    href = item.get("href") or ""
                if not nid:
                    continue
                if nid not in seen_feed:
                    seen_feed.add(nid)
                if current_target is None and nid in pending and nid not in processed:
                    current_target = (nid, href)
            added = len(seen_feed) - before
            if added == 0:
                idle += 1
            else:
                idle = 0
            if scroll_idx % 20 == 0 or added:
                print(
                    f"  scan-feed {scroll_idx}: +{added} "
                    f"total={len(seen_feed)} processed={len(processed)}",
                    flush=True,
                )

            if current_target and (not limit or len(processed) < limit):
                nid, href = current_target
                processed.add(nid)
                row = pending[nid]
                res = await crawler.fetch_one(
                    nid,
                    url_for_logging=row["url"] or "",
                    target_href=href or None,
                )
                detail = {
                    "note_id": nid,
                    "ok": res.ok,
                    "error": res.error,
                    "body": (res.body or "")[:2000] if res.ok else None,
                    "tags": res.tags if res.ok else None,
                    "author": res.author if res.ok else None,
                    "published_at": res.published_at if res.ok else None,
                    "source": "spa_click",
                }
                _commit(conn, row, detail)
                if res.ok:
                    ok += 1
                else:
                    fail += 1
                i = len(processed)
                if i % 10 == 0 or not res.ok:
                    elapsed = time.time() - t0
                    rate = i / elapsed if elapsed else 0
                    total = limit or len(pending)
                    eta = (total - i) / rate if rate else 0
                    msg = f"  {i}/{total} ok={ok} fail={fail} rate={rate:.2f}/s eta={eta:.0f}s"
                    if not res.ok:
                        msg += f"  last_error={(res.error or '')[:80]}"
                    print(msg, flush=True)
                await asyncio.sleep(pause + random.random() * pause)
                idle = 0
                continue

            if limit and len(processed) >= limit:
                break
            if idle >= 20:
                break
            await crawler._page.evaluate("window.scrollBy(0, window.innerHeight * 1.6)")
            await asyncio.sleep(1.2 + random.random() * 0.8)

        skipped_not_loaded = len(pending) - len(processed)
        print(
            f"loaded feed ids: {len(seen_feed)}; processed pending: "
            f"{len(processed)}; not loaded: {skipped_not_loaded}"
        )

    print(f"\nDONE: ok={ok} fail={fail} skipped_not_loaded={skipped_not_loaded} in {time.time()-t0:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--pause", type=float, default=1.4)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args.limit, args.retry_failed, args.pause, args.debug))


if __name__ == "__main__":
    main()