"""XHS detail-page crawler — SPA-click strategy.

Background
----------
Direct ``page.goto("https://www.xiaohongshu.com/explore/<id>?xsec_token=...")``
no longer works:

  * the ``xsec_token`` snapshotted at list-time expires within hours;
  * even when fresh, xhs's anti-bot system flags "third-party" navigations
    (no SPA referer) and serves a "Open in App" QR-code page;
  * some note types (videos, micro-cards) refuse to render Web detail at all.

What DOES work is what a human does: stay on your collection feed, click a
note card. xhs's SPA then:

  1. pushes ``/explore/<id>?xsec_token=<freshly minted>`` onto the URL,
  2. opens a modal overlay that DOM-renders the note (title / desc / images),
  3. closes back to the feed via Esc / clicking the backdrop / history.back().

So this crawler:

  1. Navigates ONCE to the user's collection page (or reuses a tab already
     parked there) and ensures the 收藏 tab is selected.
  2. For each note_id, scrolls until an anchor tagged with ``/explore/<id>``
     is visible, clicks it, waits for the modal DOM, harvests fields, then
     dismisses with Esc + history.back() so the next note can be found.

Failure modes are surfaced via :class:`DetailResult.error` instead of raised
so a batch can keep going.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

from playwright.async_api import (  # type: ignore[import-not-found]
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

from .xhs import CrawlerConfig, CrawlerError, XHS_HOSTS


# ---------------------------------------------------------------------------
# DOM probe — runs inside the page after a note modal has opened. Same
# selectors that worked for the standalone /explore/ page also work for the
# modal because xhs reuses the renderer; we just have to be liberal.
# ---------------------------------------------------------------------------

_DETAIL_PROBE_JS = r"""
() => {
  const pickText = (sels, root) => {
    const r = root || document;
    for (const s of sels) {
      const el = r.querySelector(s);
      if (el) {
        const t = (el.innerText || el.textContent || '').trim();
        if (t) return t;
      }
    }
    return null;
  };
  const pickAll = (sels, root) => {
    const r = root || document;
    for (const s of sels) {
      const els = Array.from(r.querySelectorAll(s));
      if (els.length) return els;
    }
    return [];
  };

  // Locate the modal/detail root if there is one — beats picking up stale
  // strings from the underlying feed.
  const detailRoot =
    document.querySelector(
      '#noteContainer, .note-container, [class*="noteDetailMask"], ' +
      '[class*="note-detail-mask"], [class*="note-detail"], ' +
      '[class*="noteContent"], [class*="note-content"]'
    ) || document;

  const title = pickText(['#detail-title', '.title', '[class*="title"]', 'h1'], detailRoot);
  const body = pickText(
    ['#detail-desc', '.desc', '[class*="desc"]', '[class*="content"] [class*="text"]'],
    detailRoot,
  );

  const imgEls = pickAll(
    [
      '.swiper-slide img',
      '[class*="slider"] img',
      '[class*="swiper"] img',
      '[class*="media-container"] img',
      '[class*="img-container"] img',
      'img',
    ],
    detailRoot,
  );
  const seenImg = new Set();
  const images = [];
  for (const im of imgEls) {
    let src = im.getAttribute('src') || im.getAttribute('data-src') || '';
    if (!src) continue;
    if (/avatar|emoji|icon|sprite|placeholder|qrcode/i.test(src)) continue;
    const w = im.naturalWidth || parseInt(im.getAttribute('width') || '0', 10) || 0;
    if (w && w < 80) continue;
    const key = src.split('?')[0].split('!')[0];
    if (seenImg.has(key)) continue;
    seenImg.add(key);
    images.push(src);
  }

  const author = pickText(
    [
      '.author-wrapper .username',
      '.author-wrapper .name',
      '.author .name',
      '.author-name',
      '[class*="author"] [class*="name"]',
      'a[href*="/user/profile/"]',
    ],
    detailRoot,
  );

  const tagEls = pickAll(
    [
      '.tag-content',
      '.note-tags a',
      'a[href*="/search_result?keyword="]',
      'a[href*="/page/topics/"]',
    ],
    detailRoot,
  );
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

  // xhs renders the publish info as e.g. "编辑于 06-15 上海" / "2024-09-03" /
  // "3 天前" inside .bottom-container near the body. We try several shapes.
  let published_at = pickText(
    [
      '.bottom-container .date',
      '[class*="bottom"] [class*="date"]',
      '[class*="publish"] [class*="time"]',
      '.publish-time',
      '.date',
      'span.date',
      'time',
    ],
    detailRoot,
  );
  // Some layouts put time in a generic span inside .bottom-container without a
  // class hint — sniff the text for a date-ish pattern as a last resort.
  if (!published_at && detailRoot) {
    const bottom = detailRoot.querySelector('.bottom-container, [class*="bottom"]');
    if (bottom) {
      const txt = (bottom.innerText || bottom.textContent || '').trim();
      // Patterns: 2024-09-03, 06-15, 编辑于 X, 3 天前, 昨天, 今天
      const m = txt.match(
        /(?:编辑于\s*)?(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}|\d+\s*(?:天|周|月|年)前|昨天|今天|刚刚)\s*[\u4e00-\u9fa5\w]*/
      );
      if (m) published_at = m[0].trim();
    }
  }

  // Detect the "Sorry, This Page Isn't Available Right Now." / QR-wall page.
  const html = document.body ? (document.body.innerText || '') : '';
  const isQrWall =
    /Sorry, This Page Isn'?t Available Right Now/i.test(html) ||
    /请打开小红书App扫码查看/.test(html) ||
    !!document.querySelector('canvas[class*="qrcode"], img[alt*="qrcode"]');

  // Has a real modal/detail container actually attached to the DOM?
  // We don't trust the broad fallback selectors — only the strongly-signal
  // selectors that the FEED never has count as proof of a real modal.
  const strongModal =
    document.querySelector(
      '#noteContainer, [class*="noteDetailMask"], [class*="note-detail-mask"]'
    );
  const modalOpen = !!strongModal;

  // "Did the modal really show up with USEFUL content?"
  // Feed cards already expose title + cover image, so requiring just those
  // means we can't tell "modal opened" from "we re-grabbed feed data". We
  // need EITHER:
  //   (a) the strong modal container is in DOM AND we got body text or
  //       a published_at string (both are modal-only signals), OR
  //   (b) we have a multi-image carousel (>=2 images, since cover is 1)
  //       AND author was found inside the detail root.
  const hasBody = !!(body && body.trim().length >= 8);
  const hasMultipleImages = images.length >= 2;
  const ok =
    !isQrWall &&
    (
      (modalOpen && hasBody) ||
      (modalOpen && !!title && !!published_at) ||
      (modalOpen && !!title && hasMultipleImages && !!author)
    );

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
      qr_wall: isQrWall,
      modal_open: modalOpen,
      has_body: hasBody,
      img_count: images.length,
      detail_root_class:
        detailRoot === document ? null : (detailRoot.className || detailRoot.id || 'unknown'),
    },
  };
}
"""


# Helper JS: scroll a specific note's anchor into view (or report missing).
_SCROLL_TO_NOTE_JS = r"""
(noteId) => {
  const a = document.querySelector(
    `a[href*="/${noteId}?xsec_token="], ` +
    `a[href*="/explore/${noteId}"], ` +
    `a[href*="/discovery/item/${noteId}"]`
  );
  if (!a) return { found: false };
  a.scrollIntoView({ block: 'center', behavior: 'instant' });
  const rect = a.getBoundingClientRect();
  return { found: true, top: rect.top, height: rect.height };
}
"""


_SCROLL_TO_HREF_JS = r"""
(targetHref) => {
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const a = anchors.find((el) => {
    const raw = el.getAttribute('href') || '';
    return raw === targetHref || el.href === targetHref;
  });
  if (!a) return { found: false };
  a.scrollIntoView({ block: 'center', behavior: 'instant' });
  const rect = a.getBoundingClientRect();
  return { found: true, top: rect.top, height: rect.height };
}
"""


_GET_ANCHOR_BY_HREF_JS = r"""
(targetHref) => {
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  return anchors.find((el) => {
    const raw = el.getAttribute('href') || '';
    return raw === targetHref || el.href === targetHref;
  }) || null;
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
    """Reusable detail-page fetcher (SPA-click strategy).

    Lifecycle::

        async with XhsDetailCrawler(cfg) as c:
            await c.open_collection("https://www.xiaohongshu.com/user/profile/...?tab=fav&subTab=note")
            for note_id in ids:
                res = await c.fetch_one(note_id)

    ``open_collection()`` may be called more than once — the crawler caches
    the URL it's currently parked on and only re-navigates when it changes.
    """

    # How many times we scroll the feed to find a note before giving up.
    _MAX_SCROLL_TO_FIND = 80

    # Pause after each scroll while searching for an anchor.
    _SCROLL_PAUSE_S = 0.6

    def __init__(self, config: CrawlerConfig | None = None):
        self.config = config or CrawlerConfig()
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._collection_url: str | None = None

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

        # Reuse an EXISTING xhs tab — and prefer the one that already looks
        # like a 收藏/profile page. Opening a fresh tab via new_page() has
        # been shown to trigger xhs's QR-wall anti-bot, while a tab the user
        # has been interacting with sails through. We only open a new tab
        # as a last resort.
        existing_xhs_pages: list[tuple[Page, BrowserContext]] = []
        for ctx in self._browser.contexts:
            for p in ctx.pages:
                try:
                    if any(h in (p.url or "") for h in XHS_HOSTS):
                        existing_xhs_pages.append((p, ctx))
                except Exception:
                    continue

        # Preference order:
        #   1. a tab whose URL is already on a 收藏 / profile page,
        #   2. any other xhs tab,
        #   3. a brand new tab in the first available context (fallback).
        chosen_page: Page | None = None
        chosen_ctx: BrowserContext | None = None
        for p, ctx in existing_xhs_pages:
            url_lower = (p.url or "").lower()
            if "/user/profile/" in url_lower or "tab=fav" in url_lower:
                chosen_page, chosen_ctx = p, ctx
                break
        if chosen_page is None and existing_xhs_pages:
            chosen_page, chosen_ctx = existing_xhs_pages[0]

        if chosen_page is None:
            # No xhs tab at all. Open one in the first context — but warn:
            # this path is more likely to hit the QR wall.
            if not self._browser.contexts:
                raise CrawlerError(
                    "Attached Chrome has no browser contexts. Open at "
                    "least one tab."
                )
            chosen_ctx = self._browser.contexts[0]
            chosen_page = await chosen_ctx.new_page()

        self._ctx = chosen_ctx
        self._page = chosen_page
        # NOTE: we do NOT close this page in aclose() — it belongs to the user.
        self._owns_page = chosen_page not in [p for p, _ in existing_xhs_pages]

    async def aclose(self) -> None:
        # Only close the page if WE created it (rare fallback path). The
        # user's own xhs tabs must stay open — they belong to them.
        try:
            if self._page is not None and getattr(self, "_owns_page", False):
                await self._page.close()
        except Exception:
            pass
        self._page = None
        # We attached over CDP — never close the browser; that's the user's
        # actual Chrome window.
        self._browser = None
        self._ctx = None
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

    # ---- collection navigation ------------------------------------------

    @staticmethod
    def _force_fav_query(url: str) -> str:
        """Ensure a profile URL targets the 收藏 tab.

        The bare ``/user/profile/<uid>`` URL defaults to the 笔记 tab (the
        user's own published notes) — that's NOT where favorited notes live.
        We must force ``?tab=fav&subTab=note``. Non-profile URLs are returned
        unchanged.
        """
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        try:
            parts = urlparse(url)
        except Exception:
            return url
        if "/user/profile/" not in (parts.path or ""):
            return url
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q["tab"] = "fav"
        q.setdefault("subTab", "note")
        return urlunparse(parts._replace(query=urlencode(q)))

    async def _discover_my_profile_url(self) -> str | None:
        """Find the logged-in user's own profile URL from any xhs page.

        xhs renders a sidebar/nav link to "我" pointing at
        ``/user/profile/<my-uid>``. We look for it on the current page; if the
        current page isn't on xhs, we navigate to the public homepage first.
        Returns ``None`` if nothing matches (e.g. user logged out).
        """
        assert self._page is not None
        page = self._page
        cur = (page.url or "")
        if not any(h in cur for h in XHS_HOSTS):
            try:
                await page.goto(
                    "https://www.xiaohongshu.com/explore",
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(1.0)
            except Exception:
                return None

        # The "我" link in the left sidebar points at the user's own profile.
        # We look for any anchor whose visible text is "我" and href matches
        # /user/profile/. Falls back to the first /user/profile/ link found
        # in nav/aside containers (xhs ships several layouts).
        href = await page.evaluate(
            r"""
            () => {
              const isProfile = (h) => /\/user\/profile\/[A-Za-z0-9]+/.test(h || '');
              // Pass 1: anchor whose text is exactly "我".
              const anchors = Array.from(document.querySelectorAll('a'));
              for (const a of anchors) {
                const t = (a.innerText || a.textContent || '').trim();
                if (t === '我' && isProfile(a.getAttribute('href'))) {
                  return a.href;
                }
              }
              // Pass 2: any profile link inside a left nav / aside.
              const navs = Array.from(document.querySelectorAll(
                'aside a, nav a, [class*="side"] a, [class*="user"] a'
              ));
              for (const a of navs) {
                if (isProfile(a.getAttribute('href'))) return a.href;
              }
              return null;
            }
            """
        )
        return href or None

    async def open_collection(self, collection_url: str | None = None) -> None:
        """Park our tab on the user's 收藏 feed.

        Flexible:
        * If ``collection_url`` is a profile URL, we force ``?tab=fav&subTab=note``.
        * If ``collection_url`` is None (or anything we can't recognize), we
          auto-discover the logged-in user's profile from any xhs tab and
          navigate there.

        Idempotent — if the reused tab is already on a 收藏 view, we skip
        navigation entirely (re-goto() can re-trigger xhs's anti-bot QR wall).
        """
        await self._connect()
        assert self._page is not None

        # Fast path: the tab we picked is ALREADY on a 收藏 page. Don't
        # navigate, just verify the tab is the active one. This is the
        # happiest path because it avoids any goto() at all.
        cur_url = (self._page.url or "")
        if "/user/profile/" in cur_url and (
            "tab=fav" in cur_url
            or await self._is_fav_tab_active()
        ):
            self._collection_url = cur_url
            try:
                await self._page.bring_to_front()
            except Exception:
                pass
            await self._ensure_collection_tab()
            return

        target: str | None = None
        if collection_url and "/user/profile/" in collection_url:
            target = self._force_fav_query(collection_url)
        else:
            discovered = await self._discover_my_profile_url()
            if not discovered:
                raise CrawlerError(
                    "Could not auto-discover your xhs profile. "
                    "Make sure you're logged in to xiaohongshu.com in the "
                    "Chrome window the crawler is attached to, then retry."
                )
            target = self._force_fav_query(discovered)

        if self._collection_url != target:
            await self._page.goto(target, wait_until="domcontentloaded")
            self._collection_url = target
            try:
                await self._page.bring_to_front()
            except Exception:
                pass
            # Even with tab=fav in the URL, xhs sometimes renders 笔记 first
            # then swaps once JS reads the query. Give it a beat.
            await asyncio.sleep(1.0)
        await self._ensure_collection_tab()

    async def _is_fav_tab_active(self) -> bool:
        """DOM-side check: is the 收藏 tab currently selected?"""
        assert self._page is not None
        try:
            return await self._page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll(
                    '[class*="active"], [class*="Active"], [aria-selected="true"]'
                  ));
                  return nodes.some(n => (n.textContent || '').trim() === '收藏');
                }
                """
            )
        except Exception:
            return False

    async def _ensure_collection_tab(self) -> None:
        """Verify we're on the 收藏 tab; click it if not. Raises if it fails.

        Critical to get right — landing on the 笔记 tab silently means we'd
        scroll forever looking for favorited notes that aren't there.
        """
        assert self._page is not None
        page = self._page

        async def _is_fav_active() -> bool:
            return await page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll(
                    '[class*="active"], [class*="Active"], [aria-selected="true"]'
                  ));
                  return nodes.some(n => (n.textContent || '').trim() === '收藏');
                }
                """
            )

        for attempt in range(4):
            if await _is_fav_active():
                return
            await page.evaluate(
                """
                () => {
                  const all = Array.from(document.querySelectorAll('div, span, a, li'));
                  const target = all.find(el => {
                    const t = (el.textContent || '').trim();
                    return t === '收藏' && el.children.length <= 1;
                  });
                  if (!target) return false;
                  let cur = target;
                  for (let i = 0; i < 4 && cur; i++) {
                    cur.click();
                    cur = cur.parentElement;
                  }
                  return true;
                }
                """
            )
            await asyncio.sleep(0.8 + 0.3 * attempt)

        # Final check.
        if not await _is_fav_active():
            raise CrawlerError(
                "Could not switch to the 收藏 tab on "
                f"{self._collection_url}. The page may be a 笔记/likes feed "
                "instead — verify the URL stored in raw_meta.page_url."
            )

    # ---- single-note fetch ----------------------------------------------

    async def fetch_one(
        self,
        note_id: str,
        *,
        url_for_logging: str = "",
        target_href: str | None = None,
        wait_after_click_s: float = 1.6,
        modal_timeout_ms: int = 12_000,
    ) -> DetailResult:
        """Find note_id on the current collection feed, click it, harvest."""
        if self._page is None:
            return DetailResult(
                note_id=note_id, url=url_for_logging, ok=False,
                error="crawler not connected (call __aenter__/_connect first)",
            )
        if self._collection_url is None:
            return DetailResult(
                note_id=note_id, url=url_for_logging, ok=False,
                error="open_collection() must be called before fetch_one()",
            )
        page = self._page

        try:
            # ---- 1. Find the anchor by scrolling the feed ----
            found = False
            for _ in range(self._MAX_SCROLL_TO_FIND):
                if target_href:
                    probe = await page.evaluate(_SCROLL_TO_HREF_JS, target_href)
                else:
                    probe = await page.evaluate(_SCROLL_TO_NOTE_JS, note_id)
                if probe.get("found"):
                    found = True
                    break
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
                await asyncio.sleep(
                    self._SCROLL_PAUSE_S + random.random() * 0.3
                )
            if not found:
                return DetailResult(
                    note_id=note_id, url=url_for_logging, ok=False,
                    error=(
                        f"could not locate anchor for note in "
                        f"{self._MAX_SCROLL_TO_FIND} scrolls "
                        f"(collection={self._collection_url})"
                    ),
                )

            # ---- 2. Click and wait for the modal to render ----
            #
            # IMPORTANT: xhs's anti-bot JS rejects synthetic clicks (those
            # dispatched via element.click() in page.evaluate, which produce
            # MouseEvent objects with isTrusted=false). When that happens xhs
            # serves the QR-code wall instead of opening the SPA modal.
            #
            # We instead use playwright's locator.click(), which routes through
            # CDP Input.dispatchMouseEvent → produces a real OS-level mouse
            # event with isTrusted=true. We also briefly hover first to mimic
            # a human reading the card before clicking.
            click_url_before = page.url
            anchor_element = None
            if target_href:
                handle = await page.evaluate_handle(_GET_ANCHOR_BY_HREF_JS, target_href)
                anchor_element = handle.as_element()

            if anchor_element is None:
                # Prefer the card anchor that carries xsec_token. xhs also renders
                # a bare /explore/<id> anchor in the card; clicking that often
                # lands on a 404/300031 page instead of opening the SPA modal.
                token_anchor = page.locator(f'a[href*="/{note_id}?xsec_token="]')
                if await token_anchor.count() > 0:
                  anchor = token_anchor.first
                else:
                  anchor = page.locator(
                    f'a[href*="/explore/{note_id}"], '
                    f'a[href*="/discovery/item/{note_id}"]'
                  ).first
            else:
                anchor = anchor_element
            try:
                await anchor.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            try:
                await anchor.hover(timeout=2_000)
                await asyncio.sleep(0.25 + random.random() * 0.4)
            except Exception:
                pass
            try:
                await anchor.click(timeout=4_000, delay=40 + random.random() * 60)
            except Exception:
                # Last-resort: synthetic click. Will likely trigger QR wall but
                # at least the probe will report it cleanly.
                await page.evaluate(
                    """
                    (noteId) => {
                      const a = document.querySelector(
                        `a[href*="/${noteId}?xsec_token="], ` +
                        `a[href*="/explore/${noteId}"][href*="xsec_token="], ` +
                        `a[href*="/discovery/item/${noteId}"][href*="xsec_token="], ` +
                        `a[href*="/explore/${noteId}"], ` +
                        `a[href*="/discovery/item/${noteId}"]`
                      );
                      if (a) a.click();
                    }
                    """,
                    note_id,
                )
            try:
                await page.wait_for_function(
                    """
                    (id) => {
                      if (location.href.includes('/explore/' + id) ||
                          location.href.includes('/discovery/item/' + id)) return true;
                      return !!document.querySelector(
                        '#noteContainer, [class*="noteDetailMask"], '
                        + '[class*="note-detail"], [class*="note-content"]'
                      );
                    }
                    """,
                    arg=note_id,
                    timeout=modal_timeout_ms,
                )
            except PWTimeoutError:
                pass
            await asyncio.sleep(wait_after_click_s + random.random() * 0.5)

            # ---- 3. Probe ----
            data = await page.evaluate(_DETAIL_PROBE_JS)
            cover = (data.get("images") or [None])[0]
            ok = bool(data.get("ok"))
            dbg = data.get("debug") or {}
            qr_wall = bool(dbg.get("qr_wall"))
            err: str | None = None
            if qr_wall:
                err = (
                    "xhs served the QR-code wall (note may be App-only or "
                    "the click didn't open the SPA modal)"
                )
            elif not ok:
                # Spell out WHY we rejected — these are the actual debug
                # signals from the probe so we can diagnose layout drift.
                err = (
                    f"modal_open={dbg.get('modal_open')}, "
                    f"has_body={dbg.get('has_body')}, "
                    f"img_count={dbg.get('img_count')} — "
                    "either the SPA modal didn't open or it rendered no "
                    "body/images beyond what the feed already shows"
                )

            res = DetailResult(
                note_id=note_id,
                url=page.url,
                ok=ok and not qr_wall,
                title=data.get("title"),
                body=data.get("body"),
                author=data.get("author"),
                cover=cover,
                images=data.get("images") or [],
                tags=data.get("tags") or [],
                published_at=data.get("published_at"),
                error=err,
                debug=data.get("debug") if self.config.debug_dom else None,
            )

            # ---- 4. Dismiss the modal ----
            await self._dismiss_modal(prev_url=click_url_before)
            return res

        except Exception as e:
            try:
                await self._dismiss_modal(prev_url=self._collection_url)
            except Exception:
                pass
            return DetailResult(
                note_id=note_id, url=url_for_logging, ok=False,
                error=f"{type(e).__name__}: {e}",
            )

    async def _dismiss_modal(self, *, prev_url: str | None) -> None:
        assert self._page is not None
        page = self._page
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.4)
        except Exception:
            pass
        try:
            cur = page.url
            if "/explore/" in cur or "/discovery/item/" in cur:
                await page.go_back(wait_until="domcontentloaded")
                await asyncio.sleep(0.4)
        except Exception:
            pass
        if self._collection_url and (
            "/explore/" in (page.url or "")
            or "/discovery/item/" in (page.url or "")
          or "/404" in (page.url or "")
          or "error_code=300031" in (page.url or "")
          or "website-login/error" in (page.url or "")
        ):
            try:
                await page.goto(self._collection_url, wait_until="domcontentloaded")
                await asyncio.sleep(0.4)
                await self._ensure_collection_tab()
            except Exception:
                pass


__all__ = ["XhsDetailCrawler", "DetailResult"]
