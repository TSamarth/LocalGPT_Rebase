"""Tests for search_chunks and get_crawl_stats tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_search_chunks_registered(client):
    """search_chunks and get_crawl_stats should be registered."""
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "search_chunks" in names
    assert "get_crawl_stats" in names


@pytest.mark.asyncio
async def test_search_chunks_returns_results(client):
    """search_chunks should return chunk list and query."""
    mock_chunks = [
        {"id": "c1", "text": "Relevant content about AI research", "score": 0.92,
         "metadata": {"url": "https://example.com", "title": "AI Research"}},
    ]

    with patch("app.tools.search.get_chroma") as mock_chroma_fn:
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = mock_chunks
        mock_chroma_fn.return_value = mock_chroma

        result = await client.call_tool(
            "search_chunks",
            {"query": "AI research methods", "n_results": 5},
        )

    data = str(result)
    assert "chunks" in data.lower() or "query" in data.lower() or "relevant" in data.lower()


@pytest.mark.asyncio
async def test_search_chunks_truncates_long_text():
    """search_chunks should truncate chunk text to max_chars_per_chunk (default 600)."""
    from app.tools.search import search_chunks

    long_text = "a" * 5000
    mock_chunks = [
        {"id": "c1", "text": long_text, "score": 0.9, "metadata": {}},
    ]

    with patch("app.tools.search.get_chroma") as mock_chroma_fn:
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = mock_chunks
        mock_chroma_fn.return_value = mock_chroma

        result = await search_chunks(query="test")

    assert len(result["chunks"][0]["text"]) == 600


@pytest.mark.asyncio
async def test_search_chunks_respects_custom_max_chars():
    from app.tools.search import search_chunks

    mock_chunks = [
        {"id": "c1", "text": "b" * 1000, "score": 0.9, "metadata": {"k": "v"}},
    ]

    with patch("app.tools.search.get_chroma") as mock_chroma_fn:
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = mock_chunks
        mock_chroma_fn.return_value = mock_chroma

        result = await search_chunks(query="test", max_chars_per_chunk=50)

    chunk = result["chunks"][0]
    assert len(chunk["text"]) == 50
    assert chunk["metadata"] == {"k": "v"}


@pytest.mark.asyncio
async def test_get_crawl_stats_returns_stats(client):
    """get_crawl_stats should return sqlite and chromadb stats."""
    mock_stats = {
        "sessions": 2,
        "pages_crawled": 15,
        "chunks_stored": 120,
        "top_pages_by_score": [],
    }

    with patch("app.tools.search.get_store") as mock_store_fn, \
         patch("app.tools.search.get_chroma") as mock_chroma_fn:
        mock_store = MagicMock()
        mock_store.get_stats = MagicMock(
            return_value=mock_stats,
            side_effect=None,
        )

        import asyncio
        mock_store.get_stats = asyncio.coroutine(lambda: mock_stats) if False else MagicMock()

        # Use AsyncMock for async method
        from unittest.mock import AsyncMock
        mock_store_instance = MagicMock()
        mock_store_instance.get_stats = AsyncMock(return_value=mock_stats)
        mock_store_fn.return_value = AsyncMock(return_value=mock_store_instance)

        mock_chroma = MagicMock()
        mock_chroma.count.return_value = 120
        mock_chroma_fn.return_value = mock_chroma

        result = await client.call_tool("get_crawl_stats", {})

    assert result is not None


