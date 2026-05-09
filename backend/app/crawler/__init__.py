"""XHS crawler: list-only Stage-1 ingestion via CDP-attached Chrome.

The crawler is intentionally minimal: it only collects the cheap data
visible on the user's "我的收藏" list page (note_id, title, cover, author)
and writes it to the ``note_inbox`` table. Heavy work (fetching detail HTML,
OCR, embedding, LLM extraction) only happens later when the user explicitly
promotes a row in the inbox UI.

Why CDP attach (not headless launch):

* The user's own logged-in Chrome session is already trusted by xhs.
* No need to maintain a separate cookie/state.json.
* Cookie expiry is the user's problem (re-login in their normal browser).
* xhs's Web bot detection trips far less on a real-user-driven session.

Usage::

    # 1. Launch your normal Chrome with debugging enabled (one time):
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
        --remote-debugging-port=9222 \\
        --user-data-dir="$HOME/Library/Application Support/Google/Chrome"

    # 2. Log in to xhs in that Chrome window, navigate to:
    #    https://www.xiaohongshu.com/user/profile/<your-uid>
    #    and click the "收藏" tab.

    # 3. Run the crawler — it auto-finds the xhs tab.
    python -m app.crawler.cli scan_inbox
"""
from .xhs import XhsCollectionCrawler, CrawlerConfig, CrawlerError

__all__ = ["XhsCollectionCrawler", "CrawlerConfig", "CrawlerError"]
