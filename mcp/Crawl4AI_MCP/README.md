# Crawl4AI MCP Server

A production-ready **FastMCP** server that exposes Crawl4AI's web crawling and semantic search capabilities as MCP (Model Context Protocol) tools. Built for agentic deep research workflows, especially with the **Google ADK** (Agent Development Kit).

---

## Features

| Tool | Description |
|------|-------------|
| `score_and_triage_urls` | Score & rank URLs by query relevance using BM25 + link preview |
| `crawl_url` | Single-URL crawl with two-pass content filtering |
| `crawl_many` | Batch crawl with memory-adaptive concurrency control |
| `deep_crawl` | BFS site exploration with depth/page/domain/pattern limits |
| `adaptive_crawl` | Confidence-based adaptive site exploration |
| `search_chunks` | Semantic search over stored content via ChromaDB |
| `get_crawl_stats` | Storage metrics (SQLite + ChromaDB) |

**Resources:** `crawl4ai://status`, `crawl4ai://capabilities`  
**Prompts:** `deep_research_plan`

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ollama](https://ollama.ai/) (for embeddings & semantic search)
  - `nomic-embed-text` model
  - `llama3.2` model (optional, for LLM-assisted tasks)
- Playwright browsers (installed via `crawl4ai-setup`)

---

## Setup

### 1. Clone & install dependencies

```bash
git clone <repo-url>
cd "Crawl4AI MCP"
uv sync
```

### 2. Install Playwright browsers

```bash
uv run crawl4ai-setup
```

### 3. Configure environment

Copy the example env file and edit as needed:

```bash
cp .env.example .env
```

Key settings:

```env
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=llama3.2

# Storage
CHROMA_PERSIST_DIR=./data/chroma
SQLITE_DB_PATH=./data/research.db

# Crawling
MAX_CONCURRENT_CRAWLS=5
PAGE_TIMEOUT_MS=30000
CACHE_MODE=enabled          # enabled | bypass | disabled

# Content quality
PRUNING_THRESHOLD=0.45
BM25_THRESHOLD=1.0
MIN_WORD_THRESHOLD=50

# Chunking
CHUNKING_STRATEGY=sliding_window   # sliding_window | regex | overlapping
CHUNK_WINDOW_SIZE_WORDS=200
CHUNK_OVERLAP_TOKENS=40
# CHUNK_SIZE_TOKENS is auto-derived (≈304) from the embed-model token budget.
```

See the [Configuration Reference](#configuration-reference) for the full list.

### 4. Pull Ollama models

```bash
ollama pull nomic-embed-text
ollama pull llama3.2
```

---

## Running the server

```bash
uv run python main.py
```

The server runs over **stdio** — compatible with any MCP client.

---

## Google ADK Integration

Wire the server into your ADK agent over stdio. Current `google-adk` wraps the
stdio params in `StdioConnectionParams`:

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

toolset = MCPToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "main.py"],
            cwd="/path/to/Crawl4AI_MCP",
        ),
        timeout=60,
    ),
    # Optional: expose only a subset of tools to a given agent.
    # tool_filter=["search_chunks", "get_crawl_stats"],
)

agent = LlmAgent(model="gemini-2.0-flash", name="researcher", tools=[toolset])
```

> The exact import path for `StdioConnectionParams` / `StdioServerParameters`
> tracks your installed `google-adk` version — adjust if your version differs.

### Tool response contract

Every tool returns a uniform envelope so the agent can branch reliably:

```json
{ "ok": true, "error": null, "data": { ... } }
```

Check `ok` first; read `data` on success or `error` on failure. Crawl tools
(`crawl_url`, `crawl_many`, `deep_crawl`, `adaptive_crawl`) return only a short
**preview** plus a `page_id` — the full, semantically-chunked content is stored
in SQLite + ChromaDB. Retrieve it on demand with `search_chunks` instead of
carrying whole pages in the agent's context window.

---

## Recommended Research Workflow

```
1. score_and_triage_urls(urls, query)
   → Get qualified_urls with recommended strategy

2a. adaptive_crawl(seed_url, query)     ← doc sites / open-ended research
2b. deep_crawl(seed_url, query)         ← known site structure
2c. crawl_many(urls, query)             ← batch of specific pages
2d. crawl_url(url, query)               ← single targeted page

3. search_chunks(query, n_results=15)
   → Retrieve semantically ranked content chunks

4. get_crawl_stats()
   → Review coverage and storage health
```

---

## Architecture

```
app/
├── server.py           — FastMCP entry point (stdio)
├── common.py           — DRY tool/resource/prompt registration
├── config.py           — Environment-based configuration
├── utils.py            — Shared helpers
├── tools/
│   ├── triage.py       — score_and_triage_urls
│   ├── crawl.py        — crawl_url, crawl_many
│   ├── deep_crawl.py   — deep_crawl (BFS)
│   ├── adaptive_crawl.py — adaptive_crawl
│   └── search.py       — search_chunks, get_crawl_stats
├── storage/
│   ├── sqlite_store.py — Async SQLite persistence
│   ├── chroma_store.py — ChromaDB vector store (Ollama embeddings)
│   └── chunker.py      — Crawl4AI native chunking (legacy tiktoken chunker kept for compat)
├── resources/
│   └── static.py       — MCP resources
└── prompts/
    └── research.py     — MCP prompts
```

**Storage schema:**
- `crawl_sessions` — research session metadata
- `crawled_pages` — full page content & crawl metadata
- `chunks` — content chunks with ChromaDB doc IDs
- `page_links` — discovered internal/external links per page

---

## Running Tests

```bash
uv run pytest tests/ -v
```

All 20 tests run offline (no network required — tools are tested with mocks).

### Linting

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # auto-fix
```

Both `ruff check` and the test suite run in CI (`.github/workflows/ci.yml`) on
every push to `master`/`dev` and on pull requests.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `OLLAMA_LLM_MODEL` | `llama3.2` | LLM model |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | ChromaDB persistence path |
| `SQLITE_DB_PATH` | `./data/research.db` | SQLite database path |
| `MAX_CONCURRENT_CRAWLS` | `5` | Max parallel crawl sessions |
| `PAGE_TIMEOUT_MS` | `30000` | Page load timeout (ms) |
| `CACHE_MODE` | `enabled` | Crawl4AI cache mode |
| `PRUNING_THRESHOLD` | `0.45` | Content pruning aggressiveness |
| `BM25_THRESHOLD` | `1.0` | BM25 relevance filter cutoff |
| `MIN_WORD_THRESHOLD` | `50` | Minimum words per content block |
| `EMBED_MODEL_MAX_TOKENS` | `512` | Embed model context-window limit (its own tokens) |
| `EMBED_TOKENIZER_SAFETY_FACTOR` | `0.6` | cl100k→embed-tokenizer headroom factor |
| `CHUNK_SIZE_TOKENS` | *derived* (`304`) | Pre-embedding truncation cap in cl100k tokens; auto-derived from the two vars above when unset |
| `CHUNK_OVERLAP_TOKENS` | `40` | *Legacy/unused* — superseded by `CHUNK_OVERLAP_WORDS`; not read by the active chunker |
| `CHUNKING_STRATEGY` | `sliding_window` | Crawl4AI native strategy: `sliding_window` \| `regex` \| `overlapping` |
| `CHUNK_WINDOW_SIZE_WORDS` | `200` | Window size in words (sliding_window + overlapping) |
| `CHUNK_STEP_SIZE_WORDS` | `160` | Step in words between windows (sliding_window) |
| `CHUNK_OVERLAP_WORDS` | `40` | Overlap in words between windows (overlapping) |
| `REGEX_CHUNKING_PATTERNS` | `\n\n` | Comma-separated regex split patterns (regex strategy) |
| `LLM_EXTRACTION_ENABLED` | `false` | Enable LLMExtractionStrategy during crawls |

> **Note:** `CHUNK_SIZE_TOKENS` is derived as `floor(EMBED_MODEL_MAX_TOKENS × EMBED_TOKENIZER_SAFETY_FACTOR / 8) × 8` when not set explicitly. It governs pre-embedding truncation; the primary chunk *sizing* is word-based via the `CHUNKING_STRATEGY` settings.

---

## License

MIT

