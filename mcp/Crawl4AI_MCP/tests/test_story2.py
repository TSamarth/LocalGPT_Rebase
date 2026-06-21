"""Tests for Story 2 crawl4ai MCP extensions (2.1–2.5)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _data(result):
    """Extract the tool payload from inside the ToolResponse envelope."""
    env = json.loads(result.content[0].text)
    return env["data"]


# ───────────────────────── 2.1 eTLD+1 ──────────────────────────────────────


def test_registrable_domain_handles_multilevel_suffixes():
    from app.domain import registrable_domain

    assert registrable_domain("https://blog.example.co.uk/post") == "example.co.uk"
    assert registrable_domain("https://arxiv.org/abs/1234") == "arxiv.org"
    assert registrable_domain("https://export.arxiv.org/api/query") == "arxiv.org"
    assert registrable_domain("") == ""
    assert registrable_domain("http://localhost:8000/x") == ""


@pytest.mark.asyncio
async def test_discover_attaches_etld1(client):
    from app.tools.discover import _hit

    async def _adapter(query, n):
        return [
            _hit("https://blog.example.co.uk/a", "A", "", "duckduckgo"),
            _hit("https://arxiv.org/abs/9", "B", "", "duckduckgo"),
        ]

    with patch.dict("app.tools.discover._ADAPTERS", {"duckduckgo": _adapter}, clear=True):
        result = await client.call_tool(
            "discover_urls", {"query": "q", "sources": ["duckduckgo"]}
        )

    data = _data(result)
    by_url = {u["url"]: u["etld1"] for u in data["urls"]}
    assert by_url["https://blog.example.co.uk/a"] == "example.co.uk"
    assert by_url["https://arxiv.org/abs/9"] == "arxiv.org"


@pytest.mark.asyncio
async def test_chunk_metadata_includes_etld1():
    """_persist_result must stamp the source eTLD+1 onto chunk records + metadata."""
    from app.tools import crawl as crawl_mod

    result = MagicMock()
    result.success = True
    result.url = "https://news.example.co.uk/story"
    result.status_code = 200
    result.error_message = None
    result.metadata = {"title": "T"}
    result.links = {"internal": [], "external": []}
    result.extracted_content = None
    md = MagicMock()
    md.raw_markdown = "word " * 80
    md.fit_markdown = "word " * 80
    result.markdown = md

    fake_store = MagicMock()
    fake_store.save_page = AsyncMock()
    fake_store.save_links = AsyncMock()
    fake_store.save_chunks = AsyncMock()
    fake_chroma = MagicMock()

    with patch("app.tools.crawl.get_store", AsyncMock(return_value=fake_store)), \
         patch("app.tools.crawl.get_chroma", return_value=fake_chroma):
        await crawl_mod._persist_result(result, "sess", "crawl_url", "q")

    # Chroma metadata carries etld1
    chroma_metas = fake_chroma.add_chunks.call_args.args[2]
    assert all(m["etld1"] == "example.co.uk" for m in chroma_metas)
    # SQLite chunk records carry etld1
    chunk_records = fake_store.save_chunks.call_args.args[1]
    assert all(c["etld1"] == "example.co.uk" for c in chunk_records)


# ───────────────────────── 2.2 dedup ───────────────────────────────────────


def test_cosine_basic():
    from app.tools.dedup import _cosine

    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([], [1.0]) == 0.0


@pytest.mark.asyncio
async def test_dedup_collapses_mirror_set(client):
    # 3 pages: p1 & p2 are near-identical mirrors; p3 is unrelated.
    pages = [
        {"page_id": "p1", "url": "https://a.com/x", "title": "", "chroma_doc_ids": ["c1"]},
        {"page_id": "p2", "url": "https://b.com/x", "title": "", "chroma_doc_ids": ["c2"]},
        {"page_id": "p3", "url": "https://c.com/y", "title": "", "chroma_doc_ids": ["c3"]},
    ]
    vecs = {"c1": [1.0, 0.0, 0.0], "c2": [0.999, 0.01, 0.0], "c3": [0.0, 1.0, 0.0]}

    fake_store = MagicMock()
    fake_store.list_pages_with_chunks = AsyncMock(return_value=pages)
    fake_store.mark_duplicate = AsyncMock()

    fake_chroma = MagicMock()
    fake_chroma.get_mean_vector = lambda ids: vecs[ids[0]]

    with patch("app.tools.dedup.get_store", AsyncMock(return_value=fake_store)), \
         patch("app.tools.dedup.get_chroma", return_value=fake_chroma):
        result = await client.call_tool("dedup_pages", {"session_id": "s"})

    data = _data(result)
    assert data["stats"]["mirrors_merged"] == 1
    assert data["stats"]["duplicate_groups"] == 1
    grp = data["canonical_groups"][0]
    assert grp["canonical_page_id"] == "p1"
    assert grp["mirrors"][0]["page_id"] == "p2"
    # p2 recorded as a duplicate of p1
    fake_store.mark_duplicate.assert_awaited_once_with("p2", "p1")


# ───────────────────────── 2.3 seed ingest ─────────────────────────────────


@pytest.mark.asyncio
async def test_seed_tool_registered(client):
    tools = await client.list_tools()
    assert "ingest_seeds" in [t.name for t in tools]
    assert "dedup_pages" in [t.name for t in tools]


@pytest.mark.asyncio
async def test_ingest_seed_url_forces_crawl(client):
    async def fake_crawl_url(url, query=None, session_id=None, ctx=None):
        return {"success": True, "url": url, "page_id": "pid-1"}

    with patch("app.tools.seed.crawl_url", new=AsyncMock(side_effect=fake_crawl_url)):
        result = await client.call_tool(
            "ingest_seeds", {"urls": ["https://example.com/forced"]}
        )

    data = _data(result)
    assert data["stats"]["succeeded"] == 1
    assert data["results"][0]["url"] == "https://example.com/forced"
    assert data["results"][0]["kind"] == "url"


@pytest.mark.asyncio
async def test_ingest_seed_file(client, tmp_path):
    from app.config import config

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    f = seed_dir / "note.md"
    f.write_text("# Seed Note\n\n" + ("research content " * 40), encoding="utf-8")

    fake_store = MagicMock()
    fake_store.save_page = AsyncMock()
    fake_store.save_chunks = AsyncMock()
    fake_chroma = MagicMock()

    with patch.object(config, "SEED_INGEST_DIR", str(seed_dir)), \
         patch("app.tools.seed.get_store", AsyncMock(return_value=fake_store)), \
         patch("app.tools.seed.get_chroma", return_value=fake_chroma):
        result = await client.call_tool("ingest_seeds", {"files": ["note.md"]})

    data = _data(result)
    assert data["stats"]["succeeded"] == 1
    rec = data["results"][0]
    assert rec["kind"] == "file"
    assert rec["chunks"] >= 1
    fake_store.save_page.assert_awaited()  # page persisted
    fake_store.save_chunks.assert_awaited()  # chunks persisted


@pytest.mark.asyncio
async def test_ingest_seed_file_rejects_traversal(client, tmp_path):
    from app.config import config

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()

    with patch.object(config, "SEED_INGEST_DIR", str(seed_dir)):
        result = await client.call_tool(
            "ingest_seeds", {"files": ["../../etc/passwd"]}
        )

    data = _data(result)
    assert data["stats"]["failed"] == 1
    assert "../../etc/passwd" in data["errors"]


# ───────────────────────── 2.4 PDF / arXiv ─────────────────────────────────


def test_is_pdf_url():
    from app.tools.crawl import _is_pdf_url

    assert _is_pdf_url("https://example.com/paper.pdf")
    assert _is_pdf_url("https://arxiv.org/pdf/2401.00001")
    assert not _is_pdf_url("https://arxiv.org/abs/2401.00001")
    assert not _is_pdf_url("https://example.com/article")


@pytest.mark.asyncio
async def test_crawl_url_routes_pdf_through_pdf_processor(client):
    """A .pdf URL must use the PDF path, not the browser AsyncWebCrawler."""
    pdf_result = MagicMock()
    pdf_result.success = True
    pdf_result.url = "https://example.com/x.pdf"
    pdf_result.status_code = 200
    pdf_result.error_message = None
    pdf_result.metadata = {"title": "PDF"}
    pdf_result.links = {"internal": [], "external": []}
    md = MagicMock()
    md.raw_markdown = "extracted pdf text"
    md.fit_markdown = "extracted pdf text"
    pdf_result.markdown = md

    with patch("app.tools.crawl._crawl_pdf", new=AsyncMock(return_value=pdf_result)) as pdf_fn, \
         patch("app.tools.crawl.AsyncWebCrawler") as MockBrowser, \
         patch("app.tools.crawl._persist_result", new=AsyncMock(return_value="pid")):
        result = await client.call_tool("crawl_url", {"url": "https://example.com/x.pdf"})

    pdf_fn.assert_awaited_once()
    MockBrowser.assert_not_called()  # browser path bypassed for PDFs
    data = _data(result)
    assert data["url"] == "https://example.com/x.pdf"


# ───────────────────────── 2.5 rate-limit + backoff ────────────────────────


@pytest.mark.asyncio
async def test_domain_rate_limiter_spaces_same_domain():
    import time

    from app.ratelimit import DomainRateLimiter

    rl = DomainRateLimiter(min_interval_sec=0.2)
    t0 = time.monotonic()
    await rl.acquire("https://example.com/a")  # first: no wait
    await rl.acquire("https://www.example.com/b")  # same eTLD+1: must wait ~0.2s
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.18


@pytest.mark.asyncio
async def test_rate_limiter_independent_domains_no_wait():
    import time

    from app.ratelimit import DomainRateLimiter

    rl = DomainRateLimiter(min_interval_sec=0.5)
    t0 = time.monotonic()
    await rl.acquire("https://a.com/1")
    await rl.acquire("https://b.com/1")  # different domain: no throttle
    assert (time.monotonic() - t0) < 0.3


@pytest.mark.asyncio
async def test_crawl_with_retry_backs_off_on_429():
    from app import ratelimit

    calls = {"n": 0}

    def _make(status):
        r = MagicMock()
        r.status_code = status
        r.success = status < 400
        return r

    async def do_crawl():
        calls["n"] += 1
        # First two attempts 429, third succeeds.
        return _make(429) if calls["n"] < 3 else _make(200)

    slept = []

    async def fake_sleep(d):
        slept.append(d)

    with patch.object(ratelimit.asyncio, "sleep", new=fake_sleep):
        result = await ratelimit.crawl_with_retry(
            do_crawl, max_retries=3, backoff_base=1.0
        )

    assert calls["n"] == 3
    assert result.status_code == 200
    # Exponential backoff: 1.0, then 2.0
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_crawl_with_retry_exhausts_and_returns_last():
    from app import ratelimit

    async def do_crawl():
        r = MagicMock()
        r.status_code = 429
        r.success = False
        return r

    async def fake_sleep(d):
        pass

    with patch.object(ratelimit.asyncio, "sleep", new=fake_sleep):
        result = await ratelimit.crawl_with_retry(
            do_crawl, max_retries=2, backoff_base=0.5
        )

    assert result.status_code == 429  # last result returned after retries exhausted
