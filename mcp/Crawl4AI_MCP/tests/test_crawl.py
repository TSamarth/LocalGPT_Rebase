"""Tests for crawl_url and crawl_many tools."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _patch_shared_crawler(module: str, instance):
    """Patch a tool module's shared_crawler() to yield a mock crawler instance."""
    @asynccontextmanager
    async def _cm():
        yield instance

    return patch(f"{module}.shared_crawler", _cm)


def _mock_crawl_result(url: str, success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.url = url
    result.status_code = 200 if success else 404
    result.error_message = None if success else "Not found"
    result.metadata = {"title": f"Title of {url}"}
    result.links = {"internal": [{"href": f"{url}/page2"}], "external": []}
    result.screenshot = None

    md = MagicMock()
    md.raw_markdown = f"# Content of {url}\n\nSome research content."
    md.fit_markdown = "Some research content."
    result.markdown = md
    return result


@pytest.mark.asyncio
async def test_crawl_url_success(client):
    """crawl_url should return success with markdown content."""
    mock_result = _mock_crawl_result("https://example.com")

    instance = AsyncMock()
    instance.arun = AsyncMock(return_value=mock_result)

    with _patch_shared_crawler("app.tools.crawl", instance), \
         patch("app.tools.crawl._persist_result", return_value="test-page-id"):
        result = await client.call_tool("crawl_url", {"url": "https://example.com"})

    data = str(result)
    assert "example.com" in data or "success" in data.lower()


@pytest.mark.asyncio
async def test_crawl_url_with_query(client):
    """crawl_url with query should succeed without error."""
    mock_result = _mock_crawl_result("https://example.com")

    instance = AsyncMock()
    instance.arun = AsyncMock(return_value=mock_result)

    with _patch_shared_crawler("app.tools.crawl", instance), \
         patch("app.tools.crawl._persist_result", return_value="test-page-id"):
        result = await client.call_tool(
            "crawl_url",
            {"url": "https://example.com", "query": "machine learning"},
        )

    assert result is not None


@pytest.mark.asyncio
async def test_crawl_many_empty_urls(client):
    """crawl_many with empty URL list should return empty results gracefully."""
    result = await client.call_tool("crawl_many", {"urls": []})
    data = str(result)
    assert "total" in data or "results" in data


@pytest.mark.asyncio
async def test_crawl_url_registered(client):
    """crawl_url should be registered as a tool."""
    tools = await client.list_tools()
    tool_names = [t.name for t in tools]
    assert "crawl_url" in tool_names
    assert "crawl_many" in tool_names



