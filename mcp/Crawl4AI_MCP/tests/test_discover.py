"""Tests for the discover_urls tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.tools.discover import _canonicalize, _hit


def _data(result):
    """Extract the tool payload from inside the ToolResponse envelope."""
    env = json.loads(result.content[0].text)
    return env["data"]


def _fake_adapter(hits):
    async def _adapter(query, n):
        return hits
    return _adapter


def _raising_adapter():
    async def _adapter(query, n):
        raise RuntimeError("boom")
    return _adapter


# ---------------------------------------------------------------------------
# Canonicalization (pure)
# ---------------------------------------------------------------------------

def test_canonicalize_strips_tracking_and_normalizes():
    a = _canonicalize("HTTPS://Example.com/path/?utm_source=x&b=2&a=1#frag")
    assert a == "https://example.com/path?a=1&b=2"


def test_canonicalize_collapses_equivalent_urls():
    assert _canonicalize("https://x.com/p/") == _canonicalize("https://x.com/p")
    assert _canonicalize("http://x.com:80/p") == _canonicalize("http://x.com/p")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_tool_registered(client):
    tools = await client.list_tools()
    assert "discover_urls" in [t.name for t in tools]


# ---------------------------------------------------------------------------
# Merge + de-dup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_merges_and_dedups(client):
    ddg = _fake_adapter([
        _hit("https://example.com/a", "A", "", "duckduckgo"),
        _hit("https://example.com/b/", "B", "", "duckduckgo"),  # trailing slash
    ])
    arxiv = _fake_adapter([
        _hit("https://example.com/b", "B dup", "", "arxiv"),    # dup of /b/
        _hit("https://arxiv.org/abs/1234", "Paper", "", "arxiv"),
    ])

    with patch.dict("app.tools.discover._ADAPTERS", {"duckduckgo": ddg, "arxiv": arxiv}, clear=True):
        result = await client.call_tool(
            "discover_urls",
            {"query": "test", "sources": ["duckduckgo", "arxiv"]},
        )

    data = _data(result)
    urls = [u["url"] for u in data["urls"]]
    # 3 unique URLs (the /b duplicate collapses)
    assert len(urls) == 3
    assert "https://example.com/b" in urls
    assert "https://arxiv.org/abs/1234" in urls
    # The duplicate records the second source in also_in
    b_entry = next(u for u in data["urls"] if u["url"] == "https://example.com/b")
    assert "arxiv" in b_entry["also_in"]
    # by_source counts only first-seen contributions
    assert data["by_source"]["duckduckgo"] == 2
    assert data["by_source"]["arxiv"] == 1
    assert "recommended_next_step" in data


# ---------------------------------------------------------------------------
# Resilience: one source fails, others still return
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_partial_failure(client):
    good = _fake_adapter([_hit("https://ok.com/1", "ok", "", "duckduckgo")])
    bad = _raising_adapter()

    with patch.dict("app.tools.discover._ADAPTERS", {"duckduckgo": good, "arxiv": bad}, clear=True):
        result = await client.call_tool(
            "discover_urls",
            {"query": "test", "sources": ["duckduckgo", "arxiv"]},
        )

    data = _data(result)
    assert [u["url"] for u in data["urls"]] == ["https://ok.com/1"]
    assert "arxiv" in data["errors"]
    assert "boom" in data["errors"]["arxiv"]


# ---------------------------------------------------------------------------
# triage=True passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_triage_true(client):
    ddg = _fake_adapter([_hit("https://ok.com/1", "ok", "", "duckduckgo")])
    canned_triage = {
        "qualified_urls": [{"url": "https://ok.com/1", "total_score": 0.9}],
        "disqualified_urls": [],
        "stats": {"total": 1, "qualified": 1, "disqualified": 0, "score_threshold": 0.3},
    }

    with patch.dict("app.tools.discover._ADAPTERS", {"duckduckgo": ddg}, clear=True), \
         patch("app.tools.discover.score_and_triage_urls", new=AsyncMock(return_value=dict(canned_triage))):
        result = await client.call_tool(
            "discover_urls",
            {"query": "test", "sources": ["duckduckgo"], "triage": True},
        )

    data = _data(result)
    assert data["qualified_urls"][0]["url"] == "https://ok.com/1"
    # discovery metadata block is attached
    assert data["discovery"]["discovered_total"] == 1
    assert data["discovery"]["sources_used"] == ["duckduckgo"]


# ---------------------------------------------------------------------------
# Key-/flag-gated sources skip cleanly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_serpapi_skipped_without_key():
    from app.config import config
    from app.tools.discover import _search_serpapi

    with patch.object(config, "SERPAPI_KEY", ""):
        assert await _search_serpapi("q", 5) == []


@pytest.mark.asyncio
async def test_google_serp_skipped_when_disabled():
    from app.config import config
    from app.tools.discover import _search_google_serp

    with patch.object(config, "GOOGLE_SERP_ENABLED", False):
        assert await _search_google_serp("q", 5) == []
