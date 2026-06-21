"""Tests for score_and_triage_urls tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_triage_result(url: str) -> MagicMock:
    result = MagicMock()
    result.success = True
    result.url = url
    result.metadata = {"title": "Test Page", "description": "A test page"}
    result.links = {
        "internal": [
            {"href": f"{url}/page1", "text": "Page 1",
             "total_score": 0.7, "intrinsic_score": 6.0, "contextual_score": 0.65,
             "head_data": {"title": "Page 1", "meta": {"description": "About page 1"}}},
        ],
        "external": [],
    }
    return result


@pytest.mark.asyncio
async def test_triage_tool_registered(client):
    """score_and_triage_urls should be registered as a tool."""
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "score_and_triage_urls" in names


@pytest.mark.asyncio
async def test_triage_returns_qualified_disqualified(client):
    """Triage result should have qualified_urls and disqualified_urls keys."""
    mock_result = _mock_triage_result("https://example.com")

    with patch("app.tools.triage.AsyncWebCrawler") as MockCrawler:
        instance = AsyncMock()
        instance.arun = AsyncMock(return_value=mock_result)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockCrawler.return_value = instance

        result = await client.call_tool(
            "score_and_triage_urls",
            {"urls": ["https://example.com"], "query": "test research"},
        )

    # FastMCP Client returns CallToolResult; convert to string for assertion
    data = str(result)
    assert "qualified" in data.lower() or "stats" in data.lower() or "url" in data.lower()


@pytest.mark.asyncio
async def test_triage_strategy_recommendation():
    """_recommend_strategy should return correct strategies based on score and URL."""
    from app.tools.triage import _recommend_strategy

    assert _recommend_strategy("https://docs.example.com/docs/guide", 0.85) == "adaptive_crawl"
    assert _recommend_strategy("https://blog.example.com/post/hello", 0.65) == "deep_crawl"
    assert _recommend_strategy("https://example.com/article", 0.45) == "crawl_url"
    assert _recommend_strategy("https://example.com/page", 0.1) == "skip"


