"""Tests for deep_crawl tool."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_page(url: str, internal_links=None) -> MagicMock:
    r = MagicMock()
    r.success = True
    r.url = url
    r.status_code = 200
    r.error_message = None
    r.metadata = {"title": f"Page: {url}"}
    r.screenshot = None
    r.links = {
        "internal": internal_links or [],
        "external": [],
    }
    md = MagicMock()
    md.raw_markdown = f"Content of {url}"
    md.fit_markdown = f"Content of {url}"
    r.markdown = md
    return r


@pytest.mark.asyncio
async def test_deep_crawl_registered(client):
    """deep_crawl should be registered as a tool."""
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "deep_crawl" in names


@pytest.mark.asyncio
async def test_deep_crawl_max_pages_respected(client):
    """deep_crawl should not crawl more pages than max_pages."""
    seed = "https://docs.example.com"
    # Seed page links to 10 sub-pages
    sub_links = [
        {"href": f"{seed}/page{i}", "text": f"Page {i}", "intrinsic_score": 5.0}
        for i in range(10)
    ]
    seed_page = _mock_page(seed, internal_links=sub_links)
    sub_page = _mock_page(f"{seed}/page1")

    call_count = 0

    async def mock_arun(url, config=None):
        nonlocal call_count
        call_count += 1
        if url == seed:
            return seed_page
        return sub_page

    instance = AsyncMock()
    instance.arun = mock_arun

    @asynccontextmanager
    async def _shared():
        yield instance

    with patch("app.tools.deep_crawl.shared_crawler", _shared), \
         patch("app.tools.deep_crawl._persist_result", return_value="pid"):
        result = await client.call_tool(
            "deep_crawl",
            {"seed_url": seed, "max_pages": 3, "max_depth": 1},
        )

    # Should not crawl more than max_pages
    assert call_count <= 3


@pytest.mark.asyncio
async def test_deep_crawl_page_ids_present():
    """deep_crawl pages should carry page_id, and top-level page_ids should match."""
    from app.tools.deep_crawl import deep_crawl

    seed = "https://docs.example.com"
    seed_page = _mock_page(seed)

    instance = AsyncMock()
    instance.arun = AsyncMock(return_value=seed_page)

    @asynccontextmanager
    async def _shared():
        yield instance

    with patch("app.tools.deep_crawl.shared_crawler", _shared), \
         patch("app.tools.deep_crawl._persist_result", AsyncMock(return_value="pid-1")):
        result = await deep_crawl(seed_url=seed, max_pages=1, max_depth=0)

    assert result["pages"][0]["page_id"] == "pid-1"
    assert result["page_ids"] == ["pid-1"]


@pytest.mark.asyncio
async def test_deep_crawl_publication_date_passthrough():
    """deep_crawl should pass publication_date through to _persist_result."""
    from app.tools.deep_crawl import deep_crawl

    seed = "https://docs.example.com"
    seed_page = _mock_page(seed)

    instance = AsyncMock()
    instance.arun = AsyncMock(return_value=seed_page)

    @asynccontextmanager
    async def _shared():
        yield instance

    mock_persist = AsyncMock(return_value="pid-1")

    with patch("app.tools.deep_crawl.shared_crawler", _shared), \
         patch("app.tools.deep_crawl._persist_result", mock_persist):
        await deep_crawl(seed_url=seed, max_pages=1, max_depth=0, publication_date="2024-01-01")

    assert mock_persist.call_args.kwargs["publication_date"] == "2024-01-01"


@pytest.mark.asyncio
async def test_deep_crawl_stay_on_domain():
    """_same_domain should correctly identify same-domain links."""
    from app.tools.deep_crawl import _same_domain

    assert _same_domain("https://docs.example.com/page", "https://docs.example.com/") is True
    assert _same_domain("https://other.com/page", "https://docs.example.com/") is False


@pytest.mark.asyncio
async def test_deep_crawl_pattern_filter():
    """_matches_pattern should correctly filter URLs by regex."""
    from app.tools.deep_crawl import _matches_pattern

    assert _matches_pattern("https://example.com/docs/api", ".*/docs/.*") is True
    assert _matches_pattern("https://example.com/blog/post", ".*/docs/.*") is False
    assert _matches_pattern("https://example.com/anything", None) is True

