"""XHS single-note detail-page crawler over CDP.

Companion to :mod:`xhs` (the list crawler). Given a note URL (must include
``xsec_token=...`` query string captured at list time, otherwise xhs redirects
to login), opens the page in the user's already-logged-in Chrome, waits for
the note container to render, and harvests:

  - title          (string)
  - body           (string, multi-line)
  - author         (string)
  - cover          (first image URL)
  - images         (list of image URLs)
  - tags           (list of strings, hashtag-style)
  - published_at   (ISO-ish string when xhs renders it; else None)
  - debug          (dict with selector hits — only when debug_dom is on)

Returns ``None`` if the page did not render a note (login wall, deleted note,
captcha, etc).
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

from playwright.async_api import (  # type: ignore[import-not-found]
    async_playwright,
    Browser,
    Page,
    TimeoutError as PWTimeoutError,
)

from .xhs import CrawlerConfig, CrawlerError, XHS_HOSTS


# ---------------------------------------------------------------------------
# DOM probe — runs inside the page. Tries a handful of selector variants
# because xhs ships A/B layouts for the detail view too. Always returns a
# dict; fields default to None / [] when nothing matched, so the caller can
# decide what's "good enough" to keep.
# ---------------------------------------------------------------------------

_DETAIL_PROBE_JS = r"""
() => {
  const pickText = (sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) {
        const t = (el.innerText || el.textContent || '').trim();
        if (t) return t;
      }
    }
    return null;
  };
  const pickAll = (sels) => {
    for (const s of sels) {
      const els = Array.from(document.querySelectorAll(s));
      if (els.length) return els;
    }
    return [];
  };
  const meta = (prop) => {
    const el = document.querySelector(`meta[property="${prop}"], meta[name="${prop}"]`);
    return el ? (el.getAttribute('content') || '').trim() : null;
  };

  // Title: prefer detail container then og:title.
  const title =
    pickText([
      '#detail-title',
      '.note-content .title',
      '.note-detail .title',
      '[class*="note-content"] .title',
      'h1',
    ]) || meta('og:title');

  // Body / desc.
  const body =
    pickText([
      '#detail-desc',
      '.note-content .desc',
      '.note-detail .desc',
      '[class*="note-content"] .desc',
      '[class*="note-content"] [class*="desc"]',
      '[class*="content"] [class*="desc"]',
    ]) || meta('description') || meta('og:description');

  // Images: the slider/swiper renders all of them. We dedupe and strip
  // xhs's "?imageView2/...&w=..." query so we don't accidentally grab the
  // same image at multiple resolutions.
  const imgEls = pickAll([
    '.swiper-slide img',
    '.note-slider img',
    '[class*="slider"] img',
    '[class*="swiper"] img',
    '[class*="media-container"] img',
    '#noteContainer img',
  ]);
  const seenImg = new Set();
  const images = [];
  for (const im of imgEls) {
    let src = im.getAttribute('src') || im.getAttribute('data-src') || '';
    if (!src) continue;
    // Skip avatar/icon/sprite fragments and tiny placeholders.
    if (/avatar|emoji|icon|sprite|placeholder/i.test(src)) continue;
    // Strip xhs CDN size params so dedup works across thumb/full variants.
    const key = src.split('?')[0].split('!')[0];
    if (seenImg.has(key)) continue;
    seenImg.add(key);
    images.push(src);
  }
  // og:image fallback if slider was empty
  if (images.length === 0) {
    const og = meta('og:image');
    if (og) images.push(og);
  }

  // Author: try a few namespaces.
  const author =
    pickText([
      '.author-wrapper .username',
      '.author-wrapper .name',
      '.author .name',
      '.author-name',
      '[class*="author"] [class*="name"]',
      'a[href*="/user/profile/"]',
    ]) || meta('author');

  // Tags (hashtag chips).
  const tagEls = pickAll([
    '.tag-content',
    '.note-tags a',
    'a[href*="/search_result?keyword="]',
    'a[href*="/page/topics/"]',
  ]);
  const seenTag = new Set();
  const tags = [];
  for (const t of tagEls) {
    const txt = (t.innerText || t.textContent || '').trim();
    if (!txt) continue;
    const cleaned = txt.replace(/^#/, '').trim();
    if (!cleaned || cleaned.length > 30) continue;
    if (seenTag.has(cleaned)) continue;
    seenTag.add(cleaned);
    tags.push(cleaned);
  }

  // Published date — xhs shows it near the bottom of the note as a
  // freeform string ("2024-08-15 上海" or "08-15 上海"). We just pull the
  // raw text and let the caller decide what to do with it.
  const published_at = pickText([
    '.bottom-container .date',
    '.note-content .date',
    '[class*="bottom"] [class*="date"]',
    '[class*="publish"] [class*="time"]',
    '.publish-time',
    '.date',
  ]);

  // A weak "did the note actually load?" signal: any of (title, body,
  // images.length>0). When all are empty we very likely hit a login wall.
  const ok = !!(title || body || images.length);

  return {
    ok,
    title: title || null,
    body: body || null,
    author: author || null,
    images,
    tags,
    published_at,
    debug: {
      url: location.href,
      have_note_container: !!document.querySelector(
        '#noteContainer, .note-container, .note-detail, [class*="note-content"]'
      ),
    },
  };
}
"""


@dataclass
class DetailResult:
    note_id: str
    url: str
    ok: bool
    title: str | None = None
    body: str | None = None
    author: str | None = None
    cover: str | None = None
    images: list[str] | None = None
    tags: list[str] | None = None
    published_at: str | None = None
    error: str | None = None
    debug: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class XhsDetailCrawler:
    """Reusable detail-page fetcher.

    Holds one CDP connection across many notes so we don't pay the
    connect-over-cdp cost per URL. ``aclose()`` should be awaited when done.
    """

    def __init__(self, config: CrawlerConfig | None = None):
        self.config = config or CrawlerConfig()
        self._pw = None
        self._browser: Browser | None = None
        self._page: Page | None = None  # reused, navigated per call

    async def __aenter__(self) -> "XhsDetailCrawler":
        await self._connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _connect(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(
                self.config.cdp_endpoint
            )
        except Exception as e:
            await self._pw.stop()
            self._pw = None
            raise CrawlerError(
                f"Could not attach to Chrome at {self.config.cdp_endpoint}. "
                "Launch Chrome with --remote-debugging-port=9222 first.\n"
                f"  underlying error: {e}"
            ) from e

        # Reuse the FIRST context that contains an xhs tab so we inherit the
        # user's login cookies. If none, fall back to the default context.
        target_ctx = None
        for ctx in self._browser.contexts:
            for p in ctx.pages:
                try:
                    if any(h in (p.url or "") for h in XHS_HOSTS):
                        target_ctx = ctx
                        break
                except Exception:
                    continue
            if target_ctx:
                break
        if target_ctx is None and self._browser.contexts:
            target_ctx = self._browser.contexts[0]
        if target_ctx is None:
            raise CrawlerError(
                "Attached Chrome has no browser contexts. Open at least one tab."
            )

        # Open a dedicated tab we can navigate freely without disturbing the
        # user's collection list. We *don't* reuse an existing tab because
        # navigating away would lose their scroll position on the list page.
        self._page = await target_ctx.new_page()

    async def aclose(self) -> None:
        try:
            if self._page is not None:
                await self._page.close()
        except Exception:
            pass
        self._page = None
        # We connected over CDP — DON'T close the browser, that would close
        # the user's actual Chrome window. Just stop playwright.
        self._browser = None
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

    async def fetch_one(
        self,
        note_id: str,
        url: str,
        *,
        wait_after_load_s: float = 1.5,
        timeout_ms: int = 25_000,
    ) -> DetailResult:
        """Navigate to ``url``, wait for note DOM, return DetailResult."""
        await self._connect()
        assert self._page is not None
        page = self._page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # The note body lazy-loads after DOMContentLoaded. We wait for
            # *any* of the candidate containers, then a tiny extra pause so
            # the desc block is also painted.
            try:
                await page.wait_for_selector(
                    "#noteContainer, .note-container, .note-detail, "
                    "[class*='note-content'], .swiper-slide img, "
                    "meta[property='og:title']",
                    timeout=timeout_ms,
                    state="attached",
                )
            except PWTimeoutError:
                # Probe anyway — meta tags may already be there even if the
                # SPA shell didn't finish hydrating.
                pass
            await asyncio.sleep(wait_after_load_s + random.random() * 0.6)

            data = await page.evaluate(_DETAIL_PROBE_JS)
            cover = (data.get("images") or [None])[0]
            ok = bool(data.get("ok"))
            res = DetailResult(
                note_id=note_id,
                url=url,
                ok=ok,
                title=data.get("title"),
                body=data.get("body"),
                author=data.get("author"),
                cover=cover,
                images=data.get("images") or [],
                tags=data.get("tags") or [],
                published_at=data.get("published_at"),
                debug=data.get("debug") if self.config.debug_dom else None,
            )
            if not ok:
                res.error = (
                    "detail page rendered nothing (login wall / deleted / captcha?)"
                )
            return res
        except PWTimeoutError as e:
            return DetailResult(
                note_id=note_id, url=url, ok=False,
                error=f"timeout: {e}",
            )
        except Exception as e:
            return DetailResult(
                note_id=note_id, url=url, ok=False,
                error=f"{type(e).__name__}: {e}",
            )

    async def fetch_many(
        self,
        items: list[tuple[str, str]],
        *,
        per_note_pause_s: float = 2.5,
        per_note_jitter_s: float = 1.2,
        progress_cb=None,
    ) -> list[DetailResult]:
        """Fetch a batch of (note_id, url) pairs sequentially with rate-limit jitter."""
        out: list[DetailResult] = []
        for i, (nid, url) in enumerate(items):
            res = await self.fetch_one(nid, url)
            out.append(res)
            if progress_cb:
                try:
                    progress_cb(i + 1, len(items), res)
                except Exception:
                    pass
            if i + 1 < len(items):
                await asyncio.sleep(
                    per_note_pause_s + random.random() * per_note_jitter_s
                )
        return out


__all__ = ["XhsDetailCrawler", "DetailResult"]
