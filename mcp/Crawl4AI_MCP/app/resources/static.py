"""
Static MCP resources: server status and capabilities.
"""
from __future__ import annotations

import importlib.metadata

import httpx

from app.config import config
from app.storage.sqlite_store import get_store


async def get_status() -> str:
    """
    Server health status including Crawl4AI version, Ollama connectivity,
    and storage counts.
    """
    # Crawl4AI version
    try:
        c4ai_version = importlib.metadata.version("crawl4ai")
    except Exception:
        c4ai_version = "unknown"

    # Ollama ping
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(config.OLLAMA_BASE_URL)
            ollama_ok = resp.status_code < 500
    except Exception:
        ollama_ok = False

    # SQLite stats
    try:
        store = await get_store()
        stats = await store.get_stats()
    except Exception:
        stats = {}

    # ChromaDB count
    try:
        from app.storage.chroma_store import get_chroma
        chroma_count = get_chroma().count()
    except Exception:
        chroma_count = -1

    lines = [
        "# Crawl4AI Research MCP Server",
        "",
        f"**Version:** {config.SERVER_VERSION}",
        f"**Crawl4AI:** {c4ai_version}",
        "",
        "## Connectivity",
        f"- Ollama ({config.OLLAMA_BASE_URL}): {'✅ reachable' if ollama_ok else '❌ unreachable'}",
        f"- Embed model: {config.OLLAMA_EMBED_MODEL}",
        f"- LLM model: {config.OLLAMA_LLM_MODEL}",
        "",
        "## Storage",
        f"- Sessions: {stats.get('sessions', 0)}",
        f"- Pages crawled: {stats.get('pages_crawled', 0)}",
        f"- Chunks (SQLite): {stats.get('chunks_stored', 0)}",
        f"- Chunks (ChromaDB): {chroma_count if chroma_count >= 0 else 'unavailable'}",
        "",
        "## Config",
        f"- Max concurrent crawls: {config.MAX_CONCURRENT_CRAWLS}",
        f"- Page timeout: {config.PAGE_TIMEOUT_MS}ms",
        f"- Cache mode: {config.CACHE_MODE}",
    ]
    return "\n".join(lines)


def get_capabilities() -> str:
    """
    Lists all available MCP tools, their purpose, and crawl strategies.
    """
    return """# Crawl4AI MCP Server — Capabilities

## Tools

### 1. `discover_urls`
Turn a research query into candidate URLs. Fans out concurrently to multiple
search sources (DuckDuckGo, arXiv, Semantic Scholar, and optionally SerpAPI /
Crawl4AI Google SERP), then merges and de-duplicates the results. This is the
FIRST step — always follow it with `score_and_triage_urls` (or pass
`triage=True`) before crawling.

### 2. `score_and_triage_urls`
Score and rank a list of URLs before crawling. Uses Crawl4AI's LinkPreviewConfig
with BM25 contextual scoring to identify high-value research sources and
recommend the optimal crawl strategy for each.

### 3. `crawl_url`
Crawl a single URL with query-aware content filtering (BM25 when a query is
given, else Pruning). Returns a short content preview + `page_id`; full content
is stored in SQLite + ChromaDB — retrieve it via `search_chunks`.

### 4. `crawl_many`
Crawl multiple URLs concurrently with MemoryAdaptiveDispatcher.
Accepts the qualified_urls list from triage. Stores all results incrementally.

### 5. `deep_crawl`
BFS link-following from a seed URL. Explores up to max_depth levels and
max_pages total. Best for documentation sites and blog archives.

### 6. `adaptive_crawl`
Confidence-based exploration using AdaptiveCrawler. Automatically follows
the most relevant links until target confidence is reached. Best for
open-ended research with unknown content distribution.

### 7. `ingest_seeds`
Force user-provided URLs and local files into the corpus, bypassing discovery
and triage. URLs are crawled; local files (under SEED_INGEST_DIR) are read and
stored directly. Guarantees the supplied sources appear in storage and search.

### 8. `dedup_pages`
Collapse syndicated/mirrored pages. Compares stored pages by cosine similarity
of their mean chunk embedding; pages ≥ DEDUP_COSINE_THRESHOLD are folded into the
earliest-crawled canonical page (the rest get a `duplicate_of` link).

### 9. `search_chunks`
Semantic search over stored research chunks via ChromaDB + Ollama embeddings.
Call this to retrieve relevant context for synthesis after crawling.

### 10. `get_crawl_stats`
Returns SQLite and ChromaDB storage statistics: sessions, pages, chunks,
top-scoring pages by quality score.

## Resources
- `crawl4ai://status` — Live server health and storage metrics
- `crawl4ai://capabilities` — This document

## Recommended Workflow
1. `discover_urls` → gather candidate URLs from search sources for the query
2. `score_and_triage_urls` → get qualified URLs + strategy recommendations
   (or call `discover_urls` with `triage=True` to do steps 1–2 in one call)
3. For each strategy group:
   - `adaptive_crawl` for doc sites (score ≥ 0.75)
   - `deep_crawl` for blog/wiki archives (score ≥ 0.55)
   - `crawl_many` for batches of individual pages
4. `search_chunks` to retrieve relevant content for synthesis
5. `get_crawl_stats` to verify coverage
"""

