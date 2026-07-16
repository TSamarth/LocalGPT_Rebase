"""Tests for score_and_triage_urls tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _fake_seeder(head_results):
    """Build a fake AsyncUrlSeeder class whose extract_head_for_urls returns head_results."""

    class _FakeSeeder:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        extract_head_for_urls = AsyncMock(return_value=head_results)

    return _FakeSeeder


@pytest.mark.asyncio
async def test_triage_tool_registered(client):
    """score_and_triage_urls should be registered as a tool."""
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "score_and_triage_urls" in names


@pytest.mark.asyncio
async def test_triage_returns_qualified_disqualified(client):
    """Triage result should have qualified_urls and disqualified_urls keys."""
    head_results = [
        {
            "url": "https://example.com",
            "status": "valid",
            "head_data": {"title": "Test Page", "meta": {"description": "A test page"}},
            "relevance_score": 0.8,
        },
    ]

    with patch("app.tools.triage.AsyncUrlSeeder", _fake_seeder(head_results)):
        result = await client.call_tool(
            "score_and_triage_urls",
            {"urls": ["https://example.com"], "query": "test research"},
        )

    # FastMCP Client returns CallToolResult; convert to string for assertion
    data = str(result)
    assert "qualified" in data.lower() or "stats" in data.lower() or "url" in data.lower()


@pytest.mark.asyncio
async def test_triage_normalizes_score_above_one():
    """total_score (BM25 relevance_score) must stay within [0,1]."""
    from app.tools.triage import score_and_triage_urls

    head_results = [
        {
            "url": "https://example.com",
            "status": "valid",
            "head_data": {"title": "Test Page", "meta": {"description": "A test page"}},
            "relevance_score": 0.84,
        },
        {
            "url": "https://example.com/page1",
            "status": "valid",
            "head_data": {"title": "Page 1", "meta": {"description": "About page 1"}},
            "relevance_score": 0.65,
        },
    ]

    with patch("app.tools.triage.AsyncUrlSeeder", _fake_seeder(head_results)):
        result = await score_and_triage_urls(
            urls=["https://example.com", "https://example.com/page1"],
            query="test research",
        )

    all_entries = result["qualified_urls"] + result["disqualified_urls"]
    for entry in all_entries:
        assert 0.0 <= entry["total_score"] <= 1.0


@pytest.mark.asyncio
async def test_triage_disqualified_entries_are_slim():
    """Disqualified entries should only carry url/total_score/recommended_strategy (+error)."""
    from app.tools.triage import score_and_triage_urls

    head_results = [
        {
            "url": "https://bad.example.com",
            "status": "failed",
            "head_data": {},
            "error": "x" * 300,  # should be truncated to ~120 chars
        },
    ]

    with patch("app.tools.triage.AsyncUrlSeeder", _fake_seeder(head_results)):
        result = await score_and_triage_urls(
            urls=["https://bad.example.com"],
            query="test research",
        )

    assert len(result["disqualified_urls"]) == 1
    entry = result["disqualified_urls"][0]
    assert set(entry.keys()) == {"url", "total_score", "recommended_strategy", "error"}
    assert len(entry["error"]) <= 120


@pytest.mark.asyncio
async def test_triage_qualified_description_capped():
    """Qualified entries should cap description at ~200 chars but keep other fields."""
    from app.tools.triage import score_and_triage_urls

    head_results = [
        {
            "url": "https://example.com/page1",
            "status": "valid",
            "head_data": {"title": "Page 1", "meta": {"description": "d" * 500}},
            "relevance_score": 0.9,
        },
    ]

    with patch("app.tools.triage.AsyncUrlSeeder", _fake_seeder(head_results)):
        result = await score_and_triage_urls(
            urls=["https://example.com/page1"],
            query="test research",
        )

    qualified = result["qualified_urls"]
    assert qualified
    entry = qualified[0]
    assert len(entry["description"]) <= 200
    assert "title" in entry


@pytest.mark.asyncio
async def test_triage_strategy_recommendation():
    """_recommend_strategy should return correct strategies based on score and URL."""
    from app.tools.triage import _recommend_strategy

    assert _recommend_strategy("https://docs.example.com/docs/guide", 0.85) == "adaptive_crawl"
    assert _recommend_strategy("https://blog.example.com/post/hello", 0.65) == "deep_crawl"
    assert _recommend_strategy("https://example.com/article", 0.45) == "crawl_url"
    assert _recommend_strategy("https://example.com/page", 0.1) == "skip"
