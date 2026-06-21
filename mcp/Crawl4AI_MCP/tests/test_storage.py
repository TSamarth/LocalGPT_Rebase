"""Tests for storage layer: SQLiteStore, ChromaStore, TextChunker."""
from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

# ── TextChunker ─────────────────────────────────────────────────────────────

def test_chunker_basic():
    from app.storage.chunker import TextChunker
    chunker = TextChunker(chunk_size=50, overlap=5)
    text = " ".join([f"word{i}" for i in range(200)])
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    assert all(c.token_count > 0 for c in chunks)
    assert all(c.text.strip() for c in chunks)


def test_chunker_empty_text():
    from app.storage.chunker import TextChunker
    chunker = TextChunker()
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_chunker_short_text():
    from app.storage.chunker import TextChunker
    chunker = TextChunker(chunk_size=512, overlap=50)
    chunks = chunker.chunk("Short text with just a few words.")
    assert len(chunks) == 1
    assert "Short text" in chunks[0].text


# ── SQLiteStore ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sqlite_init_and_save_page():
    from app.storage.sqlite_store import SQLiteStore
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = SQLiteStore(db_path=db_path)
        await store.init()

        await store.save_page(
            page_id="p1",
            session_id="s1",
            url="https://example.com",
            title="Example",
            raw_markdown="# Hello\n\nContent here.",
            fit_markdown="Content here.",
            strategy="crawl_url",
            total_score=0.75,
            status_code=200,
            success=True,
        )

        page = await store.get_page_by_url("https://example.com")
        assert page is not None
        assert page["title"] == "Example"
        assert page["success"] == 1
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_sqlite_stats():
    from app.storage.sqlite_store import SQLiteStore
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = SQLiteStore(db_path=db_path)
        await store.init()

        stats = await store.get_stats()
        assert "sessions" in stats
        assert "pages_crawled" in stats
        assert "chunks_stored" in stats
        assert stats["pages_crawled"] == 0
    finally:
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_sqlite_save_and_retrieve_chunks():
    from app.storage.sqlite_store import SQLiteStore
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = SQLiteStore(db_path=db_path)
        await store.init()

        await store.save_page(
            page_id="p2", session_id=None, url="https://test.com",
            title="Test", raw_markdown="content", fit_markdown="content",
        )

        chunks = [
            {"id": "c1", "chunk_index": 0, "chunk_text": "chunk one",
             "token_count": 10, "chroma_doc_id": "c1"},
            {"id": "c2", "chunk_index": 1, "chunk_text": "chunk two",
             "token_count": 8, "chroma_doc_id": "c2"},
        ]
        await store.save_chunks("p2", chunks)

        stats = await store.get_stats()
        assert stats["chunks_stored"] == 2
    finally:
        os.unlink(db_path)

