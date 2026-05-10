"""Enrich inbox notes with body + tags via the cookieless xhs HTML endpoint.

Stores results into note_inbox.raw_meta under the 'detail' key (no schema
migration). Sequential and throttle-aware: when xhs returns the 300013
"访问频繁" sentinel, the script sleeps with exponential backoff and resumes.

Usage:
    cd backend && python scripts/enrich_inbox.py [--limit N] [--retry-failed]

Idempotent: by default skips notes that already have any detail record.
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
from app.crawler.detail_http import fetch_note_detail_http  # noqa: E402


# Polite to xhs: sequential with jitter. Freshly-scanned xsec_token cohorts can
# be throttled account-wide if we hammer detail pages, so keep this slow.
SLEEP_MIN = 2.2
SLEEP_MAX = 4.2

# Backoff schedule (seconds) when we hit a 300013 throttle. Each successive
# hit waits longer; capped, then we give up and stop.
BACKOFF_SCHEDULE = [60, 120, 300, 600, 900, 1800]

COMMIT_EVERY = 20


def _is_throttle(err):
    return bool(err and ("throttled by xhs" in err or "300013" in err))


async def fetch_one(row):
    meta = json.loads(row["raw_meta"] or "{}")
    xsec = meta.get("xsec_token")
    src = meta.get("xsec_source") or "pc_collect"
    if not xsec:
        return {"note_id": row["note_id"], "ok": False, "error": "no xsec_token"}
    try:
        res = await fetch_note_detail_http(row["note_id"], xsec_token=xsec, xsec_source=src)
    except Exception as e:
        return {"note_id": row["note_id"], "ok": False, "error": f"exception: {e!r}"}
    return {
        "note_id": row["note_id"],
        "ok": res.ok,
        "error": res.error,
        "body": (res.body or "")[:2000] if res.ok else None,
        "tags": res.tags if res.ok else None,
        "author": res.author if res.ok else None,
        "published_at": res.published_at if res.ok else None,
    }


def commit_results(conn, results):
    if not results:
        return
    by_id = {r["note_id"]: r for r in results}
    src_rows = conn.execute(
        f"SELECT note_id, raw_meta FROM note_inbox WHERE note_id IN ({','.join('?' for _ in by_id)})",
        list(by_id.keys()),
    ).fetchall()
    cur = conn.cursor()
    for x in src_rows:
        nid = x["note_id"]
        meta = json.loads(x["raw_meta"] or "{}")
        new_d = by_id[nid]
        old_d = meta.get("detail") or {}
        if old_d.get("ok") and not new_d.get("ok"):
            continue
        meta["detail"] = new_d
        cur.execute(
            "UPDATE note_inbox SET raw_meta=? WHERE note_id=?",
            (json.dumps(meta, ensure_ascii=False), nid),
        )
    conn.commit()


async def main_async(limit, retry_failed):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    where = "status='pending'"
    if retry_failed:
        where += " AND raw_meta LIKE '%\"detail\"%' AND raw_meta LIKE '%\"ok\": false%'"
    else:
        where += " AND (raw_meta NOT LIKE '%\"detail\"%' OR raw_meta IS NULL)"
    sql = f"SELECT note_id, title, raw_meta FROM note_inbox WHERE {where}"
    if limit:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    print(f"to enrich: {len(rows)}")
    if not rows:
        return

    t0 = time.time()
    pending = []
    final_results = []
    backoff_idx = 0

    i = 0
    while i < len(rows):
        row = rows[i]
        r = await fetch_one(row)

        if _is_throttle(r.get("error")):
            wait = BACKOFF_SCHEDULE[min(backoff_idx, len(BACKOFF_SCHEDULE) - 1)]
            backoff_idx += 1
            print(f"  [throttled at {i}/{len(rows)}] backing off {wait}s "
                  f"(consecutive throttles: {backoff_idx})", flush=True)
            commit_results(conn, pending)
            pending = []
            if backoff_idx > len(BACKOFF_SCHEDULE):
                print("  giving up — too many throttles. Re-run later.", flush=True)
                break
            await asyncio.sleep(wait)
            continue

        backoff_idx = 0
        pending.append(r)
        final_results.append(r)
        i += 1

        if i % COMMIT_EVERY == 0:
            commit_results(conn, pending)
            pending = []
            elapsed = time.time() - t0
            ok = sum(1 for x in final_results if x.get("ok"))
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(rows) - i) / rate if rate > 0 else 0
            print(f"  {i}/{len(rows)}  ok={ok}  fail={i-ok}  rate={rate:.2f}/s  eta={eta:.0f}s", flush=True)

        await asyncio.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    commit_results(conn, pending)

    ok_total = sum(1 for x in final_results if x.get("ok"))
    print(f"\nDONE: {ok_total}/{len(final_results)} ok in {time.time()-t0:.1f}s")
    from collections import Counter
    fails = Counter()
    for x in final_results:
        if not x.get("ok"):
            fails[(x.get("error") or "")[:80]] += 1
    if fails:
        print("\nFailure reasons:")
        for reason, n in fails.most_common(10):
            print(f"  {n:>4}  {reason}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args.limit, args.retry_failed))


if __name__ == "__main__":
    main()
