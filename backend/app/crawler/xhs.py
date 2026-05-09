"""XHS collection-list crawler over CDP."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
        TimeoutError as PWTimeoutError,
    )
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "playwright is required for the xhs crawler. "
        "pip install playwright"
    ) from e


XHS_HOSTS = ("xiaohongshu.com", "www.xiaohongshu.com")
# Heuristics for "this tab is showing a collection-style feed" — anything else
# (homepage 推荐流, search results, single-note detail) is rejected so we never
# accidentally crawl the wrong thing.
COLLECTION_URL_HINTS = (
    "tab=fav",              # /user/profile/<uid>?tab=fav&subTab=note
    "subtab=note",
    "tabname=collect",      # 旧版 layout
    "subtabtype=collect",
    "tab=collect",
    "/user/profile/",       # bare profile URL — xhs hides the tab state in JS,
                            # so we trust the user opened the right tab manually
    "/board/",              # 专辑/合集页
    "fav",                  # last-resort, also matches ?type=fav etc.
)
# Note links rendered on the collection page look like:
#   /explore/<note_id>?xsec_token=xxx&xsec_source=pc_collect
NOTE_HREF_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{18,32})", re.IGNORECASE)


class CrawlerError(RuntimeError):
    pass


@dataclass
class CrawlerConfig:
    cdp_endpoint: str = "http://127.0.0.1:9222"
    # How many idle scrolls before we decide we've reached the bottom.
    max_idle_scrolls: int = 4
    # Pause between scrolls (seconds). Random jitter is added on top.
    scroll_pause_s: float = 1.4
    scroll_jitter_s: float = 0.8
    # Hard cap so a runaway scroll can't infinite-loop.
    max_scroll_iterations: int = 400
    # Incremental mode: stop after this many consecutive scrolls where every
    # item was already known. xhs sorts the collection page newest-first by
    # save time, so once we cross into the "old" zone there's no point
    # scrolling further. Only applies when scan() receives known_note_ids.
    stop_known_streak: int = 2
    # If True, the crawler logs DOM probe results so users can adapt
    # selectors when xhs changes their HTML.
    debug_dom: bool = False


@dataclass
class CrawlerResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    page_url: str = ""
    iterations: int = 0
    duplicate_seen: int = 0
    # Set when scan() exited early because the known-streak threshold tripped.
    early_stopped: bool = False


# ---------------------------------------------------------------------------
# DOM probe — runs *inside* the page via evaluate(). Returns a list of
# {note_id, url, title, cover_url, author} dicts. We try multiple selectors
# because xhs ships A/B variations of the feed renderer.
# ---------------------------------------------------------------------------

_DOM_PROBE_JS = r"""
() => {
  const items = [];
  const seen = new Set();

  // The collection feed uses a masonry layout. Each card is roughly:
  //   <section class="note-item" data-index="...">
  //     <a class="cover" href="/explore/<id>?...">
  //       <img src="..." />
  //     </a>
  //     <div class="footer">
  //       <a class="title" href="/explore/<id>?...">标题</a>
  //       <div class="author"><a>作者名</a></div>
  //     </div>
  //   </section>

  const candidates = document.querySelectorAll(
    'section.note-item, div.note-item, a[href*="/explore/"], a[href*="/discovery/item/"]'
  );

  for (const el of candidates) {
    // Find an anchor with /explore/ or /discovery/item/ — accept the el
    // itself if it's already an <a>.
    const anchor = el.tagName === 'A'
      ? el
      : el.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"]');
    if (!anchor) continue;
    const href = anchor.getAttribute('href') || '';
    const m = href.match(/\/(?:explore|discovery\/item)\/([0-9a-f]{18,32})/i);
    if (!m) continue;
    const noteId = m[1];
    if (seen.has(noteId)) continue;
    seen.add(noteId);

    // Walk up to find the card root so we can grab title/cover/author too.
    let card = el;
    if (el.tagName === 'A') {
      card = el.closest('section.note-item, div.note-item') || el.parentElement;
    }
    const titleEl = card?.querySelector('a.title, .title, .footer a, .desc') || null;
    const imgEl = card?.querySelector('img') || null;
    const authorEl = card?.querySelector('.author a, .author .name, .name') || null;

    // xhs hides xsec_token on a SECOND anchor inside the card (typically
    // the .cover anchor that points at /user/profile/<author>/<note_id>?xsec_token=...).
    // The bare /explore/<id> anchor we matched above does not carry it.
    // We scan all anchors in the card and pull the first xsec_token we find.
    let xsecToken = '';
    let xsecSource = '';
    if (card) {
      const anchors = card.querySelectorAll('a[href*="xsec_token="]');
      for (const a of anchors) {
        const h = a.getAttribute('href') || '';
        const tm = h.match(/xsec_token=([^&"]+)/);
        if (tm && tm[1]) {
          xsecToken = decodeURIComponent(tm[1]);
          const sm = h.match(/xsec_source=([^&"]+)/);
          xsecSource = sm ? decodeURIComponent(sm[1]) : '';
          break;
        }
      }
    }

    items.push({
      note_id: noteId,
      url: (() => {
        try {
          return new URL(href, location.origin || 'https://www.xiaohongshu.com').href;
        } catch (e) {
          return href.startsWith('http') ? href
                 : `https://www.xiaohongshu.com${href.startsWith('/') ? '' : '/'}${href}`;
        }
      })(),
      title: (titleEl?.innerText || titleEl?.textContent || '').trim().slice(0, 200),
      cover_url: imgEl?.getAttribute('src') || imgEl?.getAttribute('data-src') || null,
      author: (authorEl?.innerText || authorEl?.textContent || '').trim().slice(0, 80) || null,
      xsec_token: xsecToken || null,
      xsec_source: xsecSource || null,
    });
  }
  return items;
}
"""


class XhsCollectionCrawler:
    """Connects to a user-driven Chrome via CDP and harvests their 收藏 page."""

    def __init__(self, config: CrawlerConfig | None = None):
        self.config = config or CrawlerConfig()

    async def _find_xhs_page(self, browser: Browser, *, target_url: str | None = None) -> Page:
        """Pick the collection tab; raise if none. If target_url is given,
        navigate the chosen tab there first.
        """
        candidates: list[Page] = []
        for ctx in browser.contexts:
            for page in ctx.pages:
                try:
                    url = page.url or ""
                except Exception:
                    continue
                if any(h in url for h in XHS_HOSTS):
                    candidates.append(page)
        if not candidates:
            raise CrawlerError(
                "No xiaohongshu tab found in the attached Chrome. "
                "Open https://www.xiaohongshu.com in your Chrome window, "
                "log in, navigate to your profile's 收藏 tab, then re-run."
            )

        # If user passed a target URL, just take the first xhs tab and goto it.
        if target_url:
            page = candidates[0]
            await page.goto(target_url, wait_until="domcontentloaded")
            return page

        # Otherwise we MUST find a tab that looks like a collection feed.
        # Generic "xiaohongshu.com" or homepage "/" are explicitly rejected to
        # avoid crawling the 推荐 (recommendation) feed by mistake.
        for p in candidates:
            url_lower = (p.url or "").lower()
            if any(hint in url_lower for hint in COLLECTION_URL_HINTS):
                return p

        # No collection-like tab found — list what we did see, so the user
        # knows which tab needs adjustment.
        seen = "\n  - ".join(p.url for p in candidates)
        raise CrawlerError(
            "Found xiaohongshu tabs but none look like a 收藏 page. "
            "Either:\n"
            "  (a) navigate one of the tabs to your profile -> 收藏 tab "
            "(URL should contain 'tabName=collect' or similar), then re-run; or\n"
            "  (b) re-run with --url <full collection URL>.\n"
            f"Tabs seen:\n  - {seen}"
        )

    async def _ensure_collection_tab(self, page: Page) -> None:
        """Click the 收藏 tab on the profile page if it isn't already active.

        XHS doesn't reflect tab state in the URL, so we must inspect the DOM.
        We try several locator strategies (xhs ships A/B layouts), and verify
        success by waiting for the active tab text to be 收藏 — but never raise
        if we can't find it: the user may have already manually clicked it,
        in which case the existing DOM probe will still find items.
        """
        # Quick check: if there's already an "active" tab labelled 收藏, do nothing.
        already_active = await page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll(
                '[class*="active"], [class*="Active"], [aria-selected="true"]'
              ));
              return nodes.some(n => (n.textContent || '').trim() === '收藏');
            }
            """
        )
        if already_active:
            if self.config.debug_dom:
                print("[crawler] 收藏 tab already active")
            return

        # Try clicking. We use page.evaluate rather than locator() because xhs
        # often wraps the tab text in a span/div with no stable selector, and
        # we need to walk up to find the actual clickable ancestor.
        clicked = await page.evaluate(
            """
            () => {
              const all = Array.from(document.querySelectorAll('div, span, a, li'));
              const target = all.find(el => {
                const t = (el.textContent || '').trim();
                // Only leaf-ish nodes whose text IS exactly 收藏 (avoid matching
                // a parent container that happens to include the word).
                return t === '收藏' && el.children.length <= 1;
              });
              if (!target) return false;
              // Click the deepest reasonable interactive ancestor.
              let cur = target;
              for (let i = 0; i < 4 && cur; i++) {
                cur.click();
                cur = cur.parentElement;
              }
              return true;
            }
            """
        )
        if self.config.debug_dom:
            print(f"[crawler] tab click attempt: clicked={clicked}")
        if not clicked:
            print("[crawler] WARNING: could not find a 收藏 tab to click. "
                  "If you're already on the right tab manually, this is fine.")
            return

        # Give the SPA a moment to swap feeds.
        await asyncio.sleep(1.2)

    async def scan(
        self,
        *,
        progress_cb: Callable[[CrawlerResult], None] | None = None,
        target_url: str | None = None,
        known_note_ids: set[str] | None = None,
    ) -> CrawlerResult:
        cfg = self.config
        result = CrawlerResult()
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.connect_over_cdp(cfg.cdp_endpoint)
            except Exception as e:
                raise CrawlerError(
                    f"Could not attach to Chrome at {cfg.cdp_endpoint}. "
                    "Launch Chrome with --remote-debugging-port=9222 first.\n"
                    f"  underlying error: {e}"
                ) from e

            page = await self._find_xhs_page(browser, target_url=target_url)
            result.page_url = page.url

            # Bring it forward so lazy-loaded thumbnails actually paint.
            try:
                await page.bring_to_front()
            except Exception:
                pass

            # XHS renders a tab strip on the profile page (笔记 / 收藏 / 点赞)
            # but does NOT change the URL when you switch tabs. So even though
            # the URL says /user/profile/<uid>, we may still be on 笔记. Click
            # 收藏 to force the right feed before we start probing.
            await self._ensure_collection_tab(page)

            # Initial probe so we know the renderer is live.
            try:
                first = await page.evaluate(_DOM_PROBE_JS)
            except Exception as e:
                raise CrawlerError(
                    f"DOM probe failed on {page.url}. The page may not be a "
                    f"feed view, or xhs changed their layout. Error: {e}"
                ) from e
            if cfg.debug_dom:
                print(f"[crawler] initial probe found {len(first)} items on {page.url}")
            self._merge(result.items, first)
            if progress_cb:
                progress_cb(result)

            # Scroll until we stop discovering new ids for N consecutive rounds.
            idle = 0
            known_streak = 0
            import random
            for it in range(cfg.max_scroll_iterations):
                result.iterations = it + 1
                prev_count = len(result.items)
                try:
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 1.6)")
                except Exception:
                    pass
                pause = cfg.scroll_pause_s + random.random() * cfg.scroll_jitter_s
                await asyncio.sleep(pause)
                try:
                    fresh = await page.evaluate(_DOM_PROBE_JS)
                except Exception as e:
                    if cfg.debug_dom:
                        print(f"[crawler] probe error iteration {it}: {e}")
                    fresh = []
                added = self._merge(result.items, fresh)
                if cfg.debug_dom and added:
                    print(f"[crawler] iter {it}: +{added} (total {len(result.items)})")
                if added == 0:
                    idle += 1
                    if idle >= cfg.max_idle_scrolls:
                        if cfg.debug_dom:
                            print(f"[crawler] {idle} idle scrolls — done")
                        break
                else:
                    idle = 0

                # Incremental early-exit: once every visible item on this
                # screenful is already in the known set, we've crossed into
                # the "old" tail of the collection page. A short streak of
                # such rounds means we can stop.
                if known_note_ids and fresh:
                    fresh_ids = {x.get("note_id") for x in fresh if x.get("note_id")}
                    if fresh_ids and fresh_ids.issubset(known_note_ids):
                        known_streak += 1
                        if cfg.debug_dom:
                            print(
                                f"[crawler] iter {it}: all {len(fresh_ids)} ids "
                                f"already known (streak={known_streak})"
                            )
                        if known_streak >= cfg.stop_known_streak:
                            if cfg.debug_dom:
                                print(
                                    f"[crawler] known-streak reached "
                                    f"{known_streak} — incremental stop"
                                )
                            result.early_stopped = True
                            break
                    else:
                        known_streak = 0

                if progress_cb and len(result.items) != prev_count:
                    progress_cb(result)

            return result

    @staticmethod
    def _merge(into: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> int:
        """Append items not already present; return count added."""
        existing = {x["note_id"] for x in into}
        added = 0
        for item in new_items:
            nid = item.get("note_id")
            if not nid or nid in existing:
                continue
            into.append(item)
            existing.add(nid)
            added += 1
        return added
