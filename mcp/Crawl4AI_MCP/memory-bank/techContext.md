# Tech Context

## Core Technologies

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| MCP Framework | FastMCP | >=3.2 | MCP server, tool/resource/prompt registration |
| Web Crawling | Crawl4AI | >=0.8.6 | Browser automation, content extraction |
| Browser | Playwright/Chromium | via crawl4ai | Headless JS-capable crawling |
| Vector Store | ChromaDB | >=1.5 | Semantic search, Ollama embeddings |
| Relational DB | SQLite (aiosqlite) | >=0.22 | Full content, metadata, crawl history |
| Embeddings | Ollama nomic-embed-text | local | Text → vectors for ChromaDB |
| LLM (optional) | Ollama llama3.2 | local | LLM extraction (LLMExtractionStrategy) |
| Tokenizer | tiktoken cl100k_base | >=0.12 | Pre-embedding truncation + legacy chunker |
| Transport | stdio | — | MCP communication with Google ADK |

## Development Setup

### Prerequisites
1. Python 3.11+
2. uv package manager
3. Ollama running locally on port 11434
4. Pulled models: `ollama pull nomic-embed-text` + `ollama pull llama3.2`
5. Playwright browsers: `crawl4ai-setup`

### Running the Server
```bash
# Copy and configure environment
cp .env.example .env

# Run setup (installs Playwright browsers)
uv run crawl4ai-setup

# Start the MCP server (stdio)
uv run python -m app.server
```

### Running Tests
```bash
uv run pytest tests/ -v
uv run pytest tests/ --cov=app --cov-report=html
```

## Key Dependencies

### Crawl4AI Key APIs Used
- `AsyncWebCrawler` — core crawler instance
- `BrowserConfig` — headless, text_mode, light_mode for performance
- `CrawlerRunConfig` — per-crawl configuration
- `CacheMode` — ENABLED/BYPASS/DISABLED
- `DefaultMarkdownGenerator` + `BM25ContentFilter` + `PruningContentFilter` — content quality
- `LinkPreviewConfig` + `score_links=True` — URL scoring and triage
- `MemoryAdaptiveDispatcher` — safe concurrent crawling
- `AdaptiveCrawler` — confidence-based exploration
- `arun_many(stream=True)` — streaming batch results

### FastMCP Key APIs
- `FastMCP(name, instructions)` — server creation
- `@mcp.tool()` / `mcp.tool()(fn)` — tool registration
- `@mcp.resource(uri)` — resource registration
- `@mcp.prompt()` — prompt registration
- `Context` — injected into tools for logging
- `Client(mcp)` — in-memory test client

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| OLLAMA_BASE_URL | http://localhost:11434 | Ollama server URL |
| OLLAMA_EMBED_MODEL | nomic-embed-text | Embedding model |
| OLLAMA_LLM_MODEL | llama3.2 | LLM for extraction |
| CHROMA_PERSIST_DIR | ./data/chroma | ChromaDB storage path |
| SQLITE_DB_PATH | ./data/research.db | SQLite database path |
| MAX_CONCURRENT_CRAWLS | 5 | Concurrency cap |
| PAGE_TIMEOUT_MS | 30000 | Page load timeout |
| CACHE_MODE | enabled | Crawl cache behaviour |
| PRUNING_THRESHOLD | 0.45 | PruningContentFilter threshold |
| BM25_THRESHOLD | 1.0 | BM25ContentFilter threshold |
| MIN_WORD_THRESHOLD | 50 | Minimum words per content block |
| EMBED_MODEL_MAX_TOKENS | 512 | Embed model context-window limit |
| EMBED_TOKENIZER_SAFETY_FACTOR | 0.6 | cl100k→embed-tokenizer headroom |
| CHUNK_SIZE_TOKENS | *derived* 304 | Pre-embedding truncation cap (cl100k tokens) |
| CHUNK_OVERLAP_TOKENS | 40 | Legacy/unused — superseded by CHUNK_OVERLAP_WORDS |
| CHUNKING_STRATEGY | sliding_window | Native strategy: sliding_window\|regex\|overlapping |
| CHUNK_WINDOW_SIZE_WORDS | 200 | Window size in words |
| CHUNK_STEP_SIZE_WORDS | 160 | Step in words (sliding_window) |
| CHUNK_OVERLAP_WORDS | 40 | Overlap in words (overlapping) |
| REGEX_CHUNKING_PATTERNS | \n\n | Regex split patterns (regex strategy) |
| LLM_EXTRACTION_ENABLED | false | Enable LLMExtractionStrategy during crawls |
| DISCOVER_DEFAULT_SOURCES | duckduckgo,arxiv,semantic_scholar | Comma-sep sources for discover_urls |
| DISCOVER_MAX_RESULTS_PER_SOURCE | 10 | Per-source result cap |
| DISCOVER_MAX_TOTAL | 50 | Total URL cap after de-dup |
| SERPAPI_KEY | (empty) | SerpAPI key; source skipped when empty |
| SEMANTIC_SCHOLAR_API_KEY | (empty) | Optional; raises rate limits |
| GOOGLE_SERP_ENABLED | false | Opt-in Crawl4AI native Google SERP scraping |

## Google ADK Integration
The MCP server runs as a stdio subprocess launched by the ADK runtime.
ADK MCP tool config example:
```python
MCPToolset(
    connection_params=StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "app.server"],
        cwd="/path/to/crawl4ai-mcp",
        env={"OLLAMA_BASE_URL": "http://localhost:11434"},
    )
)
```

