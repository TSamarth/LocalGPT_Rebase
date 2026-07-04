"""
Shared, long-lived AsyncWebCrawler for the MCP server process (M2).

Launching a fresh Chromium browser per tool call is expensive. This module holds
ONE AsyncWebCrawler per server process and reuses it across tool calls, so the
browser is started once (lazily, on first crawl) and closed on server shutdown.

Only the standard text-mode browser is shared here. The PDF processor and the
screenshot path (which needs images loaded, i.e. text_mode disabled) still use
their own short-lived crawlers, since they require a different BrowserConfig.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from crawl4ai import AsyncWebCrawler, BrowserConfig

_crawler: Optional[AsyncWebCrawler] = None
_lock = asyncio.Lock()


def _browser_config() -> BrowserConfig:
    """The lean, research-optimised browser config shared across tool calls."""
    return BrowserConfig(headless=True, text_mode=True, light_mode=True, verbose=False)


async def get_crawler() -> AsyncWebCrawler:
    """Return the shared crawler, starting the browser lazily on first use.

    Guarded by an asyncio.Lock so concurrent tool calls only launch one browser.
    """
    global _crawler
    if _crawler is None:
        async with _lock:
            if _crawler is None:
                crawler = AsyncWebCrawler(config=_browser_config())
                await crawler.start()
                _crawler = crawler
    return _crawler


@asynccontextmanager
async def shared_crawler():
    """Async context manager yielding the shared crawler WITHOUT closing it.

    Drop-in for ``async with AsyncWebCrawler(...) as crawler:`` at call sites —
    the browser stays alive for reuse; it is closed once at server shutdown.
    """
    yield await get_crawler()


async def close_crawler() -> None:
    """Close the shared crawler if it was started. Safe to call when unused."""
    global _crawler
    if _crawler is not None:
        try:
            await _crawler.close()
        finally:
            _crawler = None
