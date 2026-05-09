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
    # If True, the crawler logs DOM probe results so users can adapt
    # selectors when xhs changes their HTML.
    debug_dom: bool = False


@dataclass
class CrawlerResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    page_url: str = ""
    iterations: int = 0
    duplicate_seen: int = 0


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
    });
  }
  return items;
}
"""


class XhsCollectionCrawler:
    """Connects to a user-driven Chrome via CDP and harvests their 收藏 page."""

    def __init__(self, config: CrawlerConfig | None = None):
        self.config = config or CrawlerConfig()

    async def _find_xhs_page(self, browser: Browser) -> Page:
        """Pick the best existing xhs tab; raise if none."""
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
        # Prefer a tab whose URL hints at the collection page.
        for p in candidates:
            if "collect" in (p.url or "") or "user/profile" in (p.url or ""):
                return p
        return candidates[0]

    async def scan(
        self,
        *,
        progress_cb: Callable[[CrawlerResult], None] | None = None,
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

            page = await self._find_xhs_page(browser)
            result.page_url = page.url

            # Bring it forward so lazy-loaded thumbnails actually paint.
            try:
                await page.bring_to_front()
            except Exception:
                pass

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
