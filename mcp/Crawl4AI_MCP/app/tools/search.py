"""
Search and stats tools for stored research content.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastmcp import Context

from app.config import config
from app.storage.chroma_store import get_chroma
from app.storage.sqlite_store import get_store


async def search_chunks(
    query: str,
    n_results: int = 10,
    session_id: Optional[str] = None,
    max_chars_per_chunk: int = 600,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Semantic search over stored research content chunks.

    Uses ChromaDB with Ollama embeddings (the configured OLLAMA_EMBED_MODEL)
    to find the most relevant content chunks matching the query. Optionally
    filter results to a specific research session.

    Args:
        query: Search query to find relevant chunks.
        n_results: Number of top results to return (default 10).
        session_id: Filter results to a specific session (optional).
        max_chars_per_chunk: Truncate each chunk's text to this many
            characters (default 600) to bound response size at small
            context windows.

    Returns:
        Dict with ranked chunks list and search metadata.
    """
    if ctx:
        await ctx.info(f"Semantic search: '{query}' (n={n_results})")

    where = {"session_id": session_id} if session_id else None

    try:
        chroma = get_chroma()
        chunks = chroma.search(query=query, n_results=n_results, where=where)
        for chunk in chunks:
            text = chunk.get("text")
            if text:
                chunk["text"] = text[:max_chars_per_chunk]
    except Exception as e:
        if ctx:
            await ctx.error(f"ChromaDB search failed: {e}")
        return {
            "chunks": [],
            "query": query,
            "total_found": 0,
            "error": str(e),
        }

    if ctx:
        await ctx.info(f"Found {len(chunks)} relevant chunks")

    return {
        "chunks": chunks,
        "query": query,
        "total_found": len(chunks),
        "session_id": session_id,
    }


async def get_crawl_stats(
    session_id: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Retrieve storage statistics for crawled research content.

    Returns counts of sessions, pages, and chunks stored in SQLite,
    ChromaDB collection size, and top-scoring pages for quality review.

    Args:
        session_id: If provided, return stats scoped to that session only.

    Returns:
        Dict with SQLite stats, ChromaDB count, and top pages.
    """
    if ctx:
        await ctx.info("Fetching crawl stats")

    store = await get_store()
    sqlite_stats = await store.get_stats()

    try:
        chroma = get_chroma()
        chroma_count = chroma.count()
    except Exception:
        chroma_count = -1  # Ollama/ChromaDB unavailable

    return {
        "sqlite": sqlite_stats,
        "chromadb": {
            "total_chunks": chroma_count,
            "embed_model": config.OLLAMA_EMBED_MODEL,
        },
        "session_id": session_id,
    }
