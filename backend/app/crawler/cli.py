"""CLI entrypoint for the xhs crawler.

Examples
--------

  # 1) Make sure your Chrome is running with debugging enabled:
  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
      --remote-debugging-port=9222 \\
      --user-data-dir="$HOME/Library/Application Support/Google/Chrome"

  # 2) In that Chrome, log in to xhs and open your collection page.

  # 3) Run:
  python -m app.crawler.cli scan_inbox            # quick run, default settings
  python -m app.crawler.cli scan_inbox --debug    # verbose probe output
  python -m app.crawler.cli scan_inbox --dry-run  # don't write to db
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from . import CrawlerConfig, CrawlerError, XhsCollectionCrawler
from ..config import settings
from ..store.sqlite_store import SqliteStore


def _print_progress(r) -> None:
    sys.stdout.write(f"\r[crawler] discovered {len(r.items):4d} notes "
                     f"(scrolls={r.iterations})  ")
    sys.stdout.flush()


async def cmd_scan_inbox(args: argparse.Namespace) -> int:
    cfg = CrawlerConfig(
        cdp_endpoint=args.cdp,
        max_idle_scrolls=args.idle,
        scroll_pause_s=args.pause,
        max_scroll_iterations=args.max_scrolls,
        debug_dom=args.debug,
    )
    crawler = XhsCollectionCrawler(cfg)
    try:
        result = await crawler.scan(
            progress_cb=None if args.debug else _print_progress,
            target_url=args.url,
        )
    except CrawlerError as e:
        sys.stdout.write("\n")
        print(f"[crawler] ERROR: {e}", file=sys.stderr)
        return 2

    sys.stdout.write("\n")
    print(f"[crawler] done. {len(result.items)} unique notes from {result.page_url}")
    if args.dry_run:
        print(f"[crawler] dry-run; first 5 items:")
        for it in result.items[:5]:
            print(f"  {it['note_id']}  {it.get('title','')[:60]}")
        return 0

    store = SqliteStore(settings.db_path)
    store.init_schema()
    payload = [
        {
            "note_id": it["note_id"],
            "url": it["url"],
            "title": it.get("title"),
            "cover_url": it.get("cover_url"),
            "author": it.get("author"),
            "saved_at": None,  # the list view doesn't expose this
            "raw_meta": {
                "discovered_via": "xhs_collection_crawler",
                "page_url": result.page_url,
                # Captured for the cookie-less HTTP detail fetcher (route B).
                # xsec_token is required by xhs's SSR endpoint and lives on a
                # secondary anchor inside each note card. Tokens expire after
                # a few hours — re-scan to refresh.
                "xsec_token": it.get("xsec_token"),
                "xsec_source": it.get("xsec_source") or "pc_user",
            },
        }
        for it in result.items
    ]
    counts = store.upsert_inbox_many(payload)
    statuses = store.inbox_status_counts()
    print(f"[crawler] db: inserted={counts['inserted']} "
          f"updated={counts['updated']}")
    print(f"[crawler] inbox status counts: {statuses}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xhskb-crawler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan_inbox", help="Scan the user's xhs collection page")
    p.add_argument("--cdp", default="http://127.0.0.1:9222",
                   help="CDP endpoint of the user's logged-in Chrome")
    p.add_argument("--idle", type=int, default=4,
                   help="Stop after N consecutive idle scrolls")
    p.add_argument("--pause", type=float, default=1.4,
                   help="Base pause (s) between scrolls (jitter is added)")
    p.add_argument("--max-scrolls", type=int, default=400,
                   help="Hard cap on scroll iterations")
    p.add_argument("--debug", action="store_true",
                   help="Verbose DOM probe logs")
    p.add_argument("--dry-run", action="store_true",
                   help="Print results without writing to db")
    p.add_argument("--url", default=None,
                   help="Optional: navigate the chosen tab to this URL before "
                        "crawling (use your full collection page URL)")

    pp = sub.add_parser(
        "promote",
        help="Run the detail-crawl + LLM ingest pipeline for one or more inbox rows",
    )
    pp.add_argument("note_ids", nargs="+",
                    help="One or more inbox note_ids to promote")
    pp.add_argument("--cdp", default="http://127.0.0.1:9222")
    pp.add_argument("--max", type=int, default=20,
                    help="Cap per invocation (default 20, hard max 50)")
    pp.add_argument("--debug", action="store_true",
                    help="Print detail-page DOM probe debug info")

    args = parser.parse_args(argv)
    if args.cmd == "promote":
        return cmd_promote(args)
    return asyncio.run(cmd_scan_inbox(args))


def cmd_promote(args: argparse.Namespace) -> int:
    from ..services.inbox_promote import promote_notes
    store = SqliteStore(settings.db_path)
    store.init_schema()
    res = promote_notes(
        store,
        args.note_ids,
        max_per_request=args.max,
        cdp_endpoint=args.cdp,
        debug_dom=args.debug,
    )
    print(f"[promote] done in {res.duration_s:.1f}s — "
          f"promoted={res.promoted} failed={res.failed} skipped={res.skipped}")
    for r in res.results:
        marker = {"promoted": "✓", "failed": "✗", "skipped": "·"}.get(r.status, "?")
        title = (r.title or "")[:60]
        line = f"  {marker} {r.status:9s}  {r.note_id}  {title}"
        if r.error:
            line += f"\n      error: {r.error}"
        print(line)
    return 0 if res.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
