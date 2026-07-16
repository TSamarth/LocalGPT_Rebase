"""Tests for adaptive_crawl tool."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_page(url: str) -> MagicMock:
    r = MagicMock()
    r.success = True
    r.url = url
    r.status_code = 200
    r.error_message = None
    r.metadata = {"title": f"Page: {url}"}
    r.screenshot = None
    r.links = {"internal": [], "external": []}
    md = MagicMock()
    md.raw_markdown = f"Content of {url}"
    md.fit_markdown = f"Content of {url}"
    r.markdown = md
    return r


def _setup(seed: str, urls):
    instance = AsyncMock()
    instance.arun = AsyncMock(side_effect=[_mock_page(u) for u in urls])

    @asynccontextmanager
    async def _shared():
        yield instance

    digest_result = MagicMock()
    digest_result.crawled_urls = urls

    adaptive_instance = MagicMock()
    adaptive_instance.digest = AsyncMock(return_value=digest_result)
    adaptive_instance.confidence = 0.9

    mock_store = AsyncMock()
    mock_chroma = MagicMock()

    return instance, _shared, adaptive_instance, mock_store, mock_chroma


@pytest.mark.asyncio
async def test_adaptive_crawl_page_ids_match_persisted_pages():
    """adaptive_crawl should return page_ids matching the number of persisted pages."""
    from app.tools.adaptive_crawl import adaptive_crawl

    seed = "https://docs.example.com"
    urls = [seed, f"{seed}/a", f"{seed}/b"]
    instance, _shared, adaptive_instance, mock_store, mock_chroma = _setup(seed, urls)

    with patch("app.tools.adaptive_crawl.shared_crawler", _shared), \
         patch("app.tools.adaptive_crawl.AdaptiveCrawler", return_value=adaptive_instance), \
         patch("app.tools.adaptive_crawl.get_store", AsyncMock(return_value=mock_store)), \
         patch("app.tools.adaptive_crawl.get_chroma", return_value=mock_chroma):
        result = await adaptive_crawl(seed_url=seed, query="test query")

    assert len(result["page_ids"]) == len(urls)
    assert result["pages_crawled"] == len(urls)


@pytest.mark.asyncio
async def test_adaptive_crawl_publication_date_in_metadata():
    """adaptive_crawl should include publication_date in chunk metadata when given."""
    from app.tools.adaptive_crawl import adaptive_crawl

    seed = "https://docs.example.com"
    urls = [seed]
    instance, _shared, adaptive_instance, mock_store, mock_chroma = _setup(seed, urls)

    with patch("app.tools.adaptive_crawl.shared_crawler", _shared), \
         patch("app.tools.adaptive_crawl.AdaptiveCrawler", return_value=adaptive_instance), \
         patch("app.tools.adaptive_crawl.get_store", AsyncMock(return_value=mock_store)), \
         patch("app.tools.adaptive_crawl.get_chroma", return_value=mock_chroma):
        await adaptive_crawl(seed_url=seed, query="test query", publication_date="2024-01-01")

    _, _, metadatas = mock_chroma.add_chunks.call_args[0]
    assert all(m["publication_date"] == "2024-01-01" for m in metadatas)


@pytest.mark.asyncio
async def test_adaptive_crawl_no_publication_date_key_when_absent():
    """adaptive_crawl should omit publication_date from chunk metadata when not given."""
    from app.tools.adaptive_crawl import adaptive_crawl

    seed = "https://docs.example.com"
    urls = [seed]
    instance, _shared, adaptive_instance, mock_store, mock_chroma = _setup(seed, urls)

    with patch("app.tools.adaptive_crawl.shared_crawler", _shared), \
         patch("app.tools.adaptive_crawl.AdaptiveCrawler", return_value=adaptive_instance), \
         patch("app.tools.adaptive_crawl.get_store", AsyncMock(return_value=mock_store)), \
         patch("app.tools.adaptive_crawl.get_chroma", return_value=mock_chroma):
        await adaptive_crawl(seed_url=seed, query="test query")

    _, _, metadatas = mock_chroma.add_chunks.call_args[0]
    assert all("publication_date" not in m for m in metadatas)
