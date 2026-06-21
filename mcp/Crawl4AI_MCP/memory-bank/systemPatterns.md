# System Patterns

## Architecture Overview

```
Google ADK Agent
    │ MCP stdio transport
    ▼
FastMCP Server (app/server.py)
    │ register_all() from app/common.py
    ├── Tools (app/tools/)
    │   ├── discover.py       → discover_urls (multi-source URL discovery)
    │   ├── triage.py         → score_and_triage_urls
    │   ├── crawl.py          → crawl_url, crawl_many
    │   ├── deep_crawl.py     → deep_crawl (BFS)
    │   ├── adaptive_crawl.py → adaptive_crawl
    │   └── search.py         → search_chunks, get_crawl_stats
    ├── Resources (app/resources/static.py)
    │   └── crawl4ai://status, crawl4ai://capabilities
    ├── Prompts (app/prompts/research.py)
    │   └── deep_research_plan
    └── Storage (app/storage/)
        ├── sqlite_store.py   → full content, metadata, crawl history
        ├── chroma_store.py   → semantic vector search (Ollama embeddings)
        └── chunker.py        → Crawl4AI native chunking strategies + legacy TextChunker
```

## Key Design Patterns

### 1. DRY Registration (common.py pattern)
All tools/resources/prompts registered in `app/common.py:register_all()`.
Both entry points (server.py + future test server) import this single function.

### 2. Tool Response Contract
Every crawl tool returns a consistent dict:
```python
{
  "success": bool,
  "url": str,
  "page_id": str,        # SQLite record ID
  "title": str,
  "raw_markdown": str,   # Full page markdown
  "fit_markdown": str,   # Quality-filtered markdown (for synthesis)
  "internal_links": list,
  "external_links": list,
  "metadata": {"word_count": int, "fit_word_count": int, "status_code": int},
  "error": str | None,
}
```

### 3. Content Filter Selection
One filter is chosen per crawl based on query presence:
- query given → `BM25ContentFilter(user_query=query)` — query-focused relevance
- no query    → `PruningContentFilter(threshold=0.45, dynamic)` — boilerplate removal

### 4. Lazy Storage Singletons
Both `get_store()` (SQLiteStore) and `get_chroma()` (ChromaStore) are lazy
singletons — they don't fail at import if Ollama/disk unavailable.
Crawl tools catch storage exceptions and continue (don't fail the crawl).

### 5. Memory-Adaptive Dispatcher
`crawl_many` uses `MemoryAdaptiveDispatcher(memory_threshold_percent=70, max_session_permit=N)`
for safe concurrent crawling that backs off under memory pressure.

### 6. Crawl Strategy Selection (triage heuristic)
```
score >= 0.75 + /docs/ or /wiki/ → adaptive_crawl
score >= 0.55 + /blog/ or /article/ → deep_crawl  
score >= 0.35 → crawl_url
else → skip
```

## Data Flow: Full Research Pipeline
```
discover_urls(query) — fans out to DuckDuckGo/arXiv/SemanticScholar/SerpAPI/GoogleSERP
    → de-duplicated URL list (canonical, with also_in cross-source tracking)
    → score_and_triage_urls (score + strategy assignment)
    → [adaptive_crawl | deep_crawl | crawl_many] per strategy group
    → _persist_result() per page:
        → SQLiteStore.save_page() + save_links() + save_chunks()
        → if result.extracted_content (LLM):
            → parse JSON blocks → ChromaStore.add_chunks()
          else:
            → chunk_text(fit_markdown) [Crawl4AI native strategy]
            → ChromaStore.add_chunks(chunks, Ollama embeddings)
    → search_chunks(query) → ChromaDB cosine similarity search
    → Agent synthesizes chunks into research output
```

## SQLite Schema (3 core tables)
- `crawl_sessions`: session grouping for multi-query research runs
- `crawled_pages`: full content + metadata per URL
- `chunks`: text chunks with ChromaDB doc IDs for cross-store linkage
- `page_links`: scored links extracted from crawled pages

## ChromaDB Design
- Single collection: `research_chunks`
- Metadata keys: url, title, session_id, query, chunk_index, strategy, page_id, extraction
- `extraction` value: chunking strategy name (e.g. "sliding_window") or "llm"
- Embedding: `nomic-embed-text` via Ollama
- Distance metric: cosine

