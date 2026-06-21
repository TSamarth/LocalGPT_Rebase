# Progress

## What Works (Implemented)
- [x] Project structure with uv, all dependencies installed
- [x] Configuration system (app/config.py) with env var loading
- [x] SQLite storage (crawl_sessions, crawled_pages, chunks, page_links)
- [x] ChromaDB store with Ollama embedding function
- [x] Crawl4AI native chunking strategies (SlidingWindow / Regex / Overlapping)
- [x] Legacy TextChunker (tiktoken) kept for backward compatibility
- [x] Tool: discover_urls (multi-source: DuckDuckGo/arXiv/SemanticScholar/SerpAPI/GoogleSERP, de-dup, opt-in triage)
- [x] Tool: score_and_triage_urls (LinkPreviewConfig + BM25 scoring)
- [x] Tool: crawl_url (single URL, two-pass content filter, opt-in LLM extraction)
- [x] Tool: crawl_many (batch, MemoryAdaptiveDispatcher, streaming, opt-in LLM extraction)
- [x] Tool: deep_crawl (BFS with depth/page limits, domain/pattern filters, opt-in LLM extraction)
- [x] Tool: adaptive_crawl (AdaptiveCrawler.digest() wrapper, native chunking)
- [x] Tool: search_chunks (ChromaDB cosine similarity search)
- [x] Tool: get_crawl_stats (SQLite + ChromaDB metrics)
- [x] Resource: crawl4ai://status
- [x] Resource: crawl4ai://capabilities
- [x] Prompt: deep_research_plan
- [x] FastMCP server (stdio transport)
- [x] common.py DRY registration
- [x] Test suite (6 test files; test_discover.py added for discover_urls)
- [x] Memory bank (all 6 files)

## What's Left / Known Issues
- [ ] AdaptiveCrawler `max_pages` param name needs verification for v0.8.6
- [ ] ADK integration — Write/test the Google ADK MCPToolset configuration
- [ ] LLM extraction E2E test — mock `result.extracted_content` and verify dual-path persist

## Known Decisions / Trade-offs
1. **Re-crawl in adaptive_crawl:** AdaptiveCrawler doesn't expose per-page results
   so we re-crawl URLs post-discovery. Minor inefficiency, correctness priority.
2. **Storage optional for crawl tools:** ChromaDB failures are swallowed.
   This keeps crawl tools working without Ollama, at cost of missing embeddings.
3. **No session auto-creation:** Sessions must be explicitly created or the
   session_id is passed as metadata only (no FK violation due to IGNORE).
4. **Chunking is word-based (Crawl4AI native):** SlidingWindowChunking uses word
   counts, not tokens. word_count is stored as `token_count` proxy in SQLite.
   ChromaStore still truncates by cl100k tokens before embedding for safety.
5. **LLM extraction is opt-in per-call:** Per-tool `use_llm_extraction` override
   takes precedence over `config.LLM_EXTRACTION_ENABLED` global flag.
