"""Tests for the xhs DOM probe — runs without a real xhs login."""
from __future__ import annotations

import asyncio
import sys

import pytest

playwright = pytest.importorskip("playwright")
from playwright.async_api import async_playwright

from app.crawler.xhs import _DOM_PROBE_JS


XHS_LIKE_HTML = """
<!DOCTYPE html><html><body>
<div class="feeds">
  <section class="note-item">
    <a class="cover" href="/explore/64a1b2c3d4e5f6789012345?xsec_token=abc">
      <img src="//cdn.example.com/aaa.jpg" />
    </a>
    <div class="footer">
      <a class="title" href="/explore/64a1b2c3d4e5f6789012345">京都祇园抹茶圣代</a>
      <div class="author"><a class="name">小红薯123</a></div>
    </div>
  </section>
  <section class="note-item">
    <a class="cover" href="/explore/64f1234567890abcdef1234?xsec_source=pc_collect">
      <img src="//cdn.example.com/bbb.jpg" />
    </a>
    <div class="footer">
      <a class="title" href="/explore/64f1234567890abcdef1234">东京涩谷景观咖啡</a>
      <div class="author"><a class="name">咖啡控</a></div>
    </div>
  </section>
  <!-- duplicate href - must dedupe -->
  <a href="/discovery/item/64f1234567890abcdef1234">dup</a>
  <!-- non-note anchor -->
  <a href="/about">irrelevant</a>
</div></body></html>
"""


async def _probe(html: str) -> list[dict]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        # Intercept xhs.com so we can give the page a real origin without
        # actually fetching xhs (which redirects and would destroy the context).
        async def _handler(route):
            await route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=html.encode("utf-8"),
            )
        await page.route("**/*", _handler)
        await page.goto("https://www.xiaohongshu.com/_test", wait_until="load")
        items = await page.evaluate(_DOM_PROBE_JS)
        await browser.close()
    return items


def _ensure_chromium_installed() -> None:
    """Skip the test (don't fail) if chromium isn't installed locally."""
    import subprocess
    try:
        # Quick existence probe — launching is the real check.
        async def _t():
            async with async_playwright() as pw:
                b = await pw.chromium.launch()
                await b.close()
        asyncio.run(_t())
    except Exception as e:
        msg = str(e)
        if "Executable doesn" in msg or "playwright install" in msg:
            pytest.skip("chromium not installed locally; run `playwright install chromium`")
        raise


def test_dom_probe_extracts_unique_notes_with_metadata():
    _ensure_chromium_installed()
    items = asyncio.run(_probe(XHS_LIKE_HTML))
    assert len(items) == 2, f"expected 2 unique notes, got {items}"
    a, b = items
    assert a["note_id"] == "64a1b2c3d4e5f6789012345"
    assert a["title"] == "京都祇园抹茶圣代"
    assert a["author"] == "小红薯123"
    assert "xiaohongshu.com" in a["url"]
    assert b["note_id"] == "64f1234567890abcdef1234"
    assert b["title"] == "东京涩谷景观咖啡"


def test_dom_probe_handles_empty_page():
    _ensure_chromium_installed()
    items = asyncio.run(_probe("<!DOCTYPE html><html><body></body></html>"))
    assert items == []


def test_dom_probe_ignores_non_note_links():
    _ensure_chromium_installed()
    html = """<!DOCTYPE html><html><body>
        <a href="/about">x</a>
        <a href="/explore/short">x</a>
        <a href="/explore/12345">x</a>
        <a href="/user/profile/abc">x</a>
    </body></html>"""
    items = asyncio.run(_probe(html))
    # All hrefs are too short / wrong format
    assert items == []
