# Active Context

## Current Status
**Phase: Multi-Source URL Discovery (Phase 10)**
Date: 2026-06-15

## What Was Just Built
Added `discover_urls` tool (`app/tools/discover.py`) — the new front of the research
pipeline. Fans out a free-text query to multiple search sources concurrently,
de-duplicates URLs by canonical form, and optionally chains into `score_and_triage_urls`.

### Changes Made (commit 9f86d10)
```
app/tools/discover.py  — New tool: discover_urls; 5 source adapters (duckduckgo,
                          arxiv, semantic_scholar, serpapi, google_serp);
                          URL canonicalization + cross-source also_in tracking;
                          triage=True passthrough shortcut
app/config.py          — 6 new fields: DISCOVER_DEFAULT_SOURCES,
                          DISCOVER_MAX_RESULTS_PER_SOURCE, DISCOVER_MAX_TOTAL,
                          SERPAPI_KEY, SEMANTIC_SCHOLAR_API_KEY, GOOGLE_SERP_ENABLED
app/common.py          — Registered discover_urls (now 8 tools total)
tests/test_discover.py — 7 tests covering de-dup, partial failure, triage passthrough,
                          key-gated source skipping
```

## Previous Phase (Phase 9 — still complete)
Replaced tiktoken TextChunker with Crawl4AI native chunking strategies + opt-in
LLM extraction. All changes documented in git history (commit 88c1d0b).

## Active Decisions Made

### 1. Single-pass BM25 over two-pass in crawl_url
For `crawl_url` without query: PruningContentFilter only.
With query: BM25ContentFilter directly (BM25 does its own structure analysis).
The "two-pass" described in planning is implemented as a choice between the two.

### 2. AdaptiveCrawler re-crawl pattern
`AdaptiveCrawler.digest()` doesn't expose per-page CrawlResult directly.
We re-crawl each discovered URL after adaptive exploration for proper storage.
This is slightly redundant but ensures consistent storage with BM25 fit_markdown.
**TODO:** Monitor Crawl4AI updates for direct result access from AdaptiveCrawler.

### 3. ChromaDB graceful degradation
ChromaDB/Ollama failures are caught and logged but don't fail crawl tools.
The server remains fully functional for crawling even without Ollama running.
Only `search_chunks` will return an error if ChromaDB is unavailable.

### 4. Session ID is caller-provided
The ADK agent passes a `session_id` string to group related crawls.
The server never auto-generates session IDs — full control stays with caller.

### 5. Chunking strategy is uniform across all tools
All 4 crawl tools use `chunk_text()` (Crawl4AI native) for consistency.
Controlled by `CHUNKING_STRATEGY` env var: `sliding_window` | `regex` | `overlapping`.
Default: `sliding_window` (window_size=200 words, step=160 words).

### 6. LLM extraction is opt-in
`use_llm_extraction` param on `crawl_url`, `crawl_many`, `deep_crawl` (default: False,
or `config.LLM_EXTRACTION_ENABLED` if not specified per call).
When enabled, `_persist_result` reads from `result.extracted_content` (LLM blocks)
instead of running the standalone chunking strategy.

### 7. TextChunker kept for backward compatibility
Legacy `TextChunker` class stays in `chunker.py` so existing tests and any
external code referencing it continue to work without changes.

## Next Steps / Open Items
1. ~~**Chunking strategy upgrade**~~ — Implemented ✅
2. ~~**Multi-source URL discovery**~~ — Implemented ✅ (discover_urls, 7 tests)
3. **Verify AdaptiveCrawler API** — `max_pages` parameter name may differ in v0.8.6
4. **ADK integration** — Write/test the Google ADK MCPToolset configuration
5. **LLM extraction E2E test** — Add a test that mocks `result.extracted_content`
   and verifies `_persist_result` takes the LLM path correctly
