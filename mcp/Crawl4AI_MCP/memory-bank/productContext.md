# Product Context

## Why This Project Exists
Agentic deep research requires automated, intelligent web crawling that goes
beyond simple single-page fetching. Research agents need to:
- Evaluate hundreds of URLs quickly to identify the most valuable sources
- Explore entire documentation sites or topic clusters systematically
- Extract high-quality, query-focused content (not raw HTML noise)
- Store and retrieve content semantically across multi-step research sessions

## Problems It Solves

### For the Google ADK Research Agent
- Provides all crawling capabilities as structured MCP tools — no ad-hoc web requests
- Returns `fit_markdown` (query-filtered content) ready for LLM synthesis
- Handles JavaScript-heavy sites via Playwright headless browser
- Manages concurrency and memory safely (MemoryAdaptiveDispatcher)

### For the Discovery/Triage Agent
- `score_and_triage_urls` replaces manual URL evaluation with automated scoring
- Recommends the optimal crawl strategy per URL (adaptive/deep/batch)
- Prevents wasted crawling of low-value URLs

### For Research Quality
- ChromaDB + SQLite hybrid storage enables both semantic and structured queries
- Session-grouped storage allows multi-run research on the same topic
- Content persists between agent runs — no re-crawling already-processed pages

## How It Works (User Flow)
1. Research Agent creates a research plan and generates search queries
2. Agent calls `discover_urls` to fan out across DuckDuckGo, arXiv, Semantic Scholar,
   SerpAPI, and/or Google SERP — returns a de-duplicated URL list with source metadata
3. Agent calls `score_and_triage_urls` (or passes `triage=True` to discover_urls)
   to rank URLs and assign crawl strategies
4. Based on strategy recommendations, the agent calls:
   - `adaptive_crawl` for documentation/wiki sites
   - `deep_crawl` for blog archives and structured content trees
   - `crawl_many` for batches of individual articles
5. As crawling progresses, content is chunked and stored in ChromaDB + SQLite
6. Agent calls `search_chunks` to retrieve the most relevant passages
7. Research Agent synthesizes chunks into the final research output

## User Experience Goals (Agent Perspective)
- Tools should return immediately actionable data (no post-processing needed)
- `fit_markdown` should be ready for direct LLM synthesis
- Errors should be informative and non-blocking (graceful degradation)
- Storage should be transparent — agent doesn't manage DB directly

