"""HTTP-based xhs note detail fetcher.

Inspired by NanmiCoder/MediaCrawler's ``get_note_by_id_from_html`` (which
itself credits ReaJason/xhs). The trick:

  1. xhs serves the public note page at ``/explore/<id>?xsec_token=...&xsec_source=pc_collect``.
  2. **WITHOUT** a cookie, the SSR response contains a ``<script>window.__INITIAL_STATE__ = {...}</script>``
     blob with the full note JSON (title, desc, tags, image_list, time,
     last_update_time, user, etc.) — no auth needed.
  3. **WITH** a logged-in cookie, xhs's anti-bot serves the SPA shell + a QR
     wall instead. So we deliberately strip cookies on this request.

This approach avoids:
  * playwright/CDP entirely — no browser, no clicks, no QR walls
  * xhs's signed API (X-S/X-T) which would need the xhshow JS lib
  * the ``isTrusted=false`` synthetic-click detection that breaks SPA mode

Caveats:
  * ``xsec_token`` from the inbox row expires within hours — if expired, the
    request returns the QR-wall page; caller should re-scan the inbox to refresh.
  * xhs occasionally tweaks the ``__INITIAL_STATE__`` shape; we defensively
    walk multiple key paths.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

from .detail import DetailResult


# Browser-mimicking headers. cookie is intentionally omitted.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# xhs prepends ``undefined`` to literal strings as a JS value-mangling trick.
# We strip these before json.loads.
_INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL,
)


def _strip_xhs_undefined(s: str) -> str:
    """xhs serializes ``undefined`` as a bare JS token. Replace with null
    so the blob becomes valid JSON."""
    return re.sub(r"\bundefined\b", "null", s)


def _ms_to_iso(ms: int | float | None) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _walk_first(d: dict[str, Any], keys: list[str]) -> Any:
    """Return ``d[k]`` for the first present key in ``keys``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_note_card(initial_state: dict[str, Any], note_id: str) -> dict[str, Any] | None:
    """Pull the note_card object out of __INITIAL_STATE__.

    Layout (as of late 2025):
      noteData.noteDetailMap[<note_id>].note  -> {title, desc, time, user, image_list, tag_list, last_update_time, ...}
    Older fallbacks tried if the primary path is missing.
    """
    # Primary path
    nd = initial_state.get("note") or initial_state.get("noteData") or {}
    detail_map = nd.get("noteDetailMap") or nd.get("note_detail_map") or {}
    if note_id in detail_map:
        entry = detail_map[note_id]
        if isinstance(entry, dict):
            note = entry.get("note")
            if isinstance(note, dict) and note:
                return note
    # Fallback: sometimes the first/only entry is keyed by something else.
    if isinstance(detail_map, dict) and detail_map:
        first = next(iter(detail_map.values()))
        if isinstance(first, dict):
            n = first.get("note")
            if isinstance(n, dict) and n:
                return n
    # Very old layout
    if isinstance(nd.get("noteInfo"), dict):
        return nd["noteInfo"]
    return None


def _to_detail_result(note_id: str, url: str, note: dict[str, Any]) -> DetailResult:
    title = (note.get("title") or "").strip()
    body = (note.get("desc") or "").strip()
    user = note.get("user") or {}
    author = user.get("nickname") or user.get("nick_name") or user.get("name")

    images: list[str] = []
    # xhs uses camelCase (imageList) on the SSR endpoint; snake_case (image_list)
    # on the signed API. Accept both.
    img_list = note.get("imageList") or note.get("image_list") or []
    for im in img_list:
        if not isinstance(im, dict):
            continue
        u = (
            im.get("urlDefault")
            or im.get("url_default")
            or im.get("url")
            or (im.get("infoList") or im.get("info_list") or [{}])[0].get("url")
        )
        if u:
            images.append(u)
    cover = images[0] if images else None

    tags: list[str] = []
    for t in note.get("tagList") or note.get("tag_list") or []:
        if isinstance(t, dict):
            name = (t.get("name") or "").strip()
            if name:
                tags.append(name)

    # ``time`` is publish ms; ``lastUpdateTime`` is edit ms.
    published_iso = _ms_to_iso(_walk_first(note, ["time", "publishTime", "publish_time"]))

    return DetailResult(
        note_id=note_id,
        url=url,
        ok=bool(title and (body or images)),
        title=title or None,
        body=body or None,
        author=author,
        cover=cover,
        images=images,
        tags=tags,
        published_at=published_iso,
        error=None,
        debug={
            "source": "html",
            "img_count": len(images),
            "has_body": bool(body and len(body.strip()) >= 8),
            "last_update_ms": note.get("lastUpdateTime") or note.get("last_update_time"),
        },
    )


async def fetch_note_detail_http(
    note_id: str,
    *,
    xsec_token: str,
    xsec_source: str = "pc_collect",
    timeout_s: float = 12.0,
) -> DetailResult:
    """Fetch a note detail via the cookie-less HTML endpoint.

    Args:
        note_id: 24-hex note id
        xsec_token: token captured from the inbox/feed anchor href
        xsec_source: usually ``pc_collect`` for a note discovered via 收藏 feed,
            ``pc_search`` for search, ``pc_feed`` for homefeed. Pass through.
        timeout_s: request timeout
    """
    qs = urllib.parse.urlencode({
        "xsec_token": xsec_token,
        "xsec_source": xsec_source,
    })
    url = f"https://www.xiaohongshu.com/explore/{note_id}?{qs}"

    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        headers=_DEFAULT_HEADERS,
    ) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            return DetailResult(
                note_id=note_id, url=url, ok=False,
                error=f"http error: {type(e).__name__}: {e}",
            )

    if resp.status_code != 200:
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error=f"http {resp.status_code}: {resp.text[:200]}",
        )

    # Detect IP-level throttle: xhs 302s to /website-login/error?error_code=300013
    # ("访问频繁，请稍后再试"). The final page is the login error shell.
    final_url = str(resp.url)
    if "website-login/error" in final_url or "error_code=300013" in final_url:
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error="throttled by xhs (300013: 访问频繁) — back off and retry later",
        )

    html = resp.text

    # Detect QR wall served when xsec_token expired or xhs didn't like us.
    if (
        "Sorry, This Page Isn'" in html
        or "请打开小红书App扫码查看" in html
        or "qrcode" in html and "__INITIAL_STATE__" not in html
    ):
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error="xhs served the QR wall (xsec_token likely expired — re-scan inbox)",
        )

    m = _INITIAL_STATE_RE.search(html)
    if not m:
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error="window.__INITIAL_STATE__ not found in HTML (xhs layout changed?)",
        )

    raw = _strip_xhs_undefined(m.group(1))
    try:
        initial_state = json.loads(raw)
    except json.JSONDecodeError as e:
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error=f"failed to parse __INITIAL_STATE__: {e}",
        )

    note = _extract_note_card(initial_state, note_id)
    if not note:
        # Some notes return a state shell with no note (deleted, app-only, etc).
        return DetailResult(
            note_id=note_id, url=url, ok=False,
            error="note_card missing from __INITIAL_STATE__ (deleted or app-only?)",
        )

    return _to_detail_result(note_id, url, note)


__all__ = ["fetch_note_detail_http"]
