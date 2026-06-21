# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to work in this project
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## About this project

### What this is

A FastMCP (stdio) server that exposes Crawl4AI web crawling + ChromaDB semantic search as MCP tools, built for agentic deep-research workflows driven by the Google ADK. Python 3.11+, managed with `uv`.

### Commands

```bash
uv sync                          # install deps (incl. dev group)
uv run crawl4ai-setup            # install Playwright browsers (one-time)
uv run python main.py            # run the MCP server over stdio
uv run pytest tests/ -v          # full suite (offline — crawls are mocked)
uv run pytest tests/test_crawl.py::test_name -v   # single test
uv run python e2e_test.py        # end-to-end test (hits the network + Ollama)
```

External runtime deps: **Ollama** must be running (`nomic-embed-text` for embeddings, `llama3.2` for optional LLM extraction). Pull with `ollama pull nomic-embed-text`.

### Architecture

**Entry flow:** `main.py` → imports `mcp` from `app/server.py` → `app/server.py` builds the `FastMCP` instance and calls `register_all(mcp)` from `app/common.py`. `common.py` is the single registration point — **add every new tool/resource/prompt there**, not via decorators scattered across modules. Tool functions in `app/tools/` are plain async functions registered with `mcp.tool()` in `common.py`.

**The intended research pipeline** (tools are designed to be chained in this order):
1. `score_and_triage_urls` — BM25 + link-preview scoring to rank candidate URLs and recommend a crawl strategy.
2. one of `adaptive_crawl` / `deep_crawl` / `crawl_many` / `crawl_url` — actually fetch + filter content.
3. `search_chunks` — semantic retrieval over everything stored so far.
4. `get_crawl_stats` — storage health.

**Content filtering (the core value-add of the crawl tools):** `_build_run_config` (`app/tools/crawl.py`) selects a single content filter by query presence — `BM25ContentFilter` when a `query` is given (focuses on the topic), otherwise `PruningContentFilter` (strips boilerplate). `deep_crawl` and `adaptive_crawl` make the same choice independently. Only `fit_markdown` (the filtered output) is chunked and embedded; `raw_markdown` is also persisted for reference.

**Storage layer (`app/storage/`)** — accessed only through lazy singletons, never instantiated directly:
- `get_store()` (async) → `SQLiteStore` over aiosqlite. Tables: `crawl_sessions`, `crawled_pages`, `page_links`, `chunks`. Schema lives in `CREATE_TABLES_SQL` in `sqlite_store.py`.
- `get_chroma()` → `ChromaStore`, a ChromaDB persistent client using Ollama embeddings (cosine space, collection `research_chunks`). Each SQLite chunk stores its `chroma_doc_id` to link the two stores.

**Chunking** (`app/storage/chunker.py`): the primary path is `chunk_text()`, which delegates to **Crawl4AI native word-based strategies** (`sliding_window` default, or `regex` / `overlapping`) selected by `config.CHUNKING_STRATEGY`. `token_count` is a word-count proxy here. The `TextChunker` class is a deprecated tiktoken sliding-window kept only for backward-compat/tests — don't build on it.

**Two chunking origins in `_persist_result`** (`crawl.py`): if `result.extracted_content` is set (LLM extraction was enabled), chunks come from parsing that JSON via `_parse_llm_extracted_chunks`; otherwise `fit_markdown` is chunked with the native strategy. LLM extraction is opt-in via `config.LLM_EXTRACTION_ENABLED` or a per-call `use_llm_extraction` arg.

### Configuration

All config is a single `config` singleton in `app/config.py` (a frozen-ish `@dataclass` loaded from env / `.env`). Add new settings there as `field(default_factory=...)` using the `_env` / `_env_int` / `_env_float` helpers. Note `CHUNK_SIZE_TOKENS` is *derived* from `EMBED_MODEL_MAX_TOKENS × EMBED_TOKENIZER_SAFETY_FACTOR` when not set explicitly — the safety factor exists because tiktoken under-counts vs. the BERT-style embedding tokenizer. See README's Configuration Reference table for the full variable list.

### Testing conventions

Tests use FastMCP's in-memory `Client(mcp)` fixture (`tests/conftest.py`) to call tools as a real MCP client would, with `crawl4ai` mocked so the suite runs fully offline. `asyncio_mode = "auto"` is set, so `async def test_*` works without explicit markers. When adding a tool, register it in `common.py` and test it through the `client` fixture.

<!-- headroom:learn:start -->
## Headroom Learned Patterns
*Auto-generated by `headroom learn` on 2026-06-14 — do not edit manually*

### File Paths
*~4,000 tokens/session saved*
To inspect Crawl4AI's native chunking/extraction internals, read the installed package directly: `.venv/Lib/site-packages/crawl4ai/chunking_strategy.py`, `extraction_strategy.py`, and `utils.py`. Native strategies (RegexChunking, word/sliding-window, `chunk_token_threshold`) live there. You can also refer to Crawl4AI skill.

### File Paths
*~2,500 tokens/session saved*
`app/tools/crawl.py` (~18.7KB) is the largest and most-read source file — the core crawl/filter/persist logic. Read it once and target sections rather than re-reading whole.

### File Paths
*~1,200 tokens/session saved*
Memory Bank docs live in `memory-bank/` at repo root (projectbrief.md, productContext.md, activeContext.md, systemPatterns.md, techContext.md, progress.md) — NOT in `.claude/`. Reading them under `.claude/` returns file_not_found.

<!-- headroom:learn:end -->
