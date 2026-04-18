# Phased Implementation Plan

A 5-phase implementation plan designed to avoid integration hell. Each phase ends with a working demo that integrates with all prior phases. No phase ships without proving it works with everything before it.

---

## Guiding Principles

1. **Incremental Integration**: Every phase ends with a demo that exercises the full stack built so far.
2. **Real-World Testing**: All tests hit real APIs and real internet — no mocking of network calls (see [testing-strategy.md](testing-strategy.md)).
3. **Configurable Defaults**: Embedding model is configurable via `config.py` with `all-MiniLM-L6-v2` as default.
4. **Working Software Over Documentation**: Each phase produces runnable code, not just specs.

---

## Phase 1: Foundation — Config, Logging, Storage

**Goal**: A working storage layer that can chunk text, embed it, store it, and retrieve it semantically.

### Tasks

| # | Task | File(s) | Description |
|---|------|---------|-------------|
| 1.1 | Configuration module | `config.py` | Pydantic settings. Configurable `embedding_model` (default `all-MiniLM-L6-v2`), DB paths, chunking params, concurrency. Loads from `.env`. |
| 1.2 | Logging setup | `logging_config.py` | `structlog` configuration. JSON output for machine parsing, pretty output for dev. Log levels via env var. |
| 1.3 | Text chunker | `storage/chunker.py` | 512-token chunks, 50-token overlap, paragraph-boundary-aware. Returns chunks with metadata (char_start, char_end, token_count). |
| 1.4 | ChromaDB wrapper | `storage/vector_store.py` | `VectorStore` class: create collection, add documents with embeddings, query with optional metadata filters, delete collection. Uses `sentence-transformers` for embedding with model from config. |
| 1.5 | SQLite wrapper | `storage/metadata_store.py` | `MetadataStore` class: sessions table, sources table, CRUD operations, deduplication by content hash, reliability scoring. |
| 1.6 | `.env.example` | `.env.example` | All configurable env vars with defaults and comments. |
| 1.7 | `requirements.txt` | `requirements.txt` | All dependencies for Phase 1. |

### Tests (Phase 1)

All tests use real ChromaDB (temp dir), real SQLite (temp file), and real `all-MiniLM-L6-v2` embeddings.

| Test File | What It Proves |
|-----------|---------------|
| `tests/unit/test_chunker.py` | Chunking logic: boundaries, overlap, edge cases, unicode |
| `tests/unit/test_vector_store.py` | ChromaDB operations: add, query, filter, dedup, persistence |
| `tests/unit/test_metadata_store.py` | SQLite operations: sessions, sources, dedup, reliability |
| `tests/integration/test_storage_pipeline.py` | Full round-trip: chunk → embed → store → retrieve by semantic query |

### Demo 1: Storage Round-Trip

```bash
python -m local_research_agent.demos.demo_storage "Mongol invasion of Japan"
```

**What it does**:
1. Takes hardcoded sample text about the Mongol invasion
2. Chunks it with `TextChunker`
3. Embeds and stores in ChromaDB + SQLite
4. Queries with "What caused the Mongol invasion?" 
5. Prints retrieved chunks ranked by relevance

**Success criteria**: Retrieved chunks are semantically relevant to the query, metadata is correct in SQLite.

### Phase 1 Exit Criteria
- [ ] All Phase 1 tests pass with real embeddings and real storage
- [ ] Demo 1 runs successfully and retrieves relevant chunks
- [ ] Config loads from `.env` with sensible defaults
- [ ] Structured logs appear in console

---

## Phase 2: Search & Crawl Tools

**Goal**: Working search and crawl pipeline that feeds into Phase 1 storage. No agents yet — tools are exercised directly.

### Tasks

| # | Task | File(s) | Description |
|---|------|---------|-------------|
| 2.1 | DDG search tool | `tools/ddg_search.py` | Wraps `duckduckgo-search`. Returns `{status, results: [{title, url, snippet}]}`. Configurable `max_results`. |
| 2.2 | SerpAPI search tool | `tools/serp_search.py` | Wraps `google-search-results`. Graceful degradation when no API key. Returns same structure as DDG. |
| 2.3 | Scholar search tool | `tools/scholar_search.py` | Hits Semantic Scholar API via `httpx`. Returns papers with title, abstract, citation count, URL. |
| 2.4 | Crawl4AI tool | `tools/crawl_tool.py` | `crawl_urls(urls, query)` → uses `BM25ContentFilter`, `arun_many()` with `max_concurrent=3`. Returns markdown content per URL. |
| 2.5 | Store tool | `tools/store_tool.py` | Takes crawled content + metadata → chunks → embeds → stores in ChromaDB + SQLite. Deduplicates by content hash. |
| 2.6 | Retrieve tool | `tools/retrieve_tool.py` | Semantic search over stored chunks. Filters by source_type, session, min reliability. Returns chunks + source metadata. |
| 2.7 | File writer tool | `tools/file_writer.py` | Writes markdown string to `reports/` directory with timestamped filename. |

### Tests (Phase 2)

**All search and crawl tests hit real internet.** Tests use `pytest-retry` (max 2 retries) to handle transient API flakiness.

| Test File | What It Proves |
|-----------|---------------|
| `tests/unit/test_ddg_search.py` | Real DDG search returns results with expected structure |
| `tests/unit/test_serp_search.py` | SerpAPI returns results (or graceful error with no key) |
| `tests/unit/test_scholar_search.py` | Real Semantic Scholar API returns paper metadata |
| `tests/unit/test_crawl_tool.py` | Real Crawl4AI crawls a known URL and returns markdown |
| `tests/unit/test_store_tool.py` | Store tool chunks + embeds + persists (real storage) |
| `tests/unit/test_retrieve_tool.py` | Retrieve tool returns relevant results (real storage + embeddings) |
| `tests/integration/test_search_crawl_pipeline.py` | DDG search → Crawl4AI → Store → Retrieve. Full pipeline. |

### Demo 2: Search-to-Storage Pipeline

```bash
python -m local_research_agent.demos.demo_search_pipeline "Mongol invasion of Japan 1274"
```

**What it does**:
1. Searches DDG + Semantic Scholar for the query (real internet)
2. Crawls top 5 URLs with Crawl4AI + BM25 filtering
3. Stores all crawled content in ChromaDB + SQLite (Phase 1 storage)
4. Retrieves the most relevant chunks for "What were the consequences of the Mongol invasion?"
5. Prints: search results found, URLs crawled, chunks stored, top retrieved chunks

**Success criteria**: Real web content is searched, crawled, stored, and semantically retrievable.

### Integration with Phase 1
- Store tool uses `VectorStore` and `MetadataStore` from Phase 1
- Retrieve tool uses the same
- All config flows through `config.py`
- All operations logged via `structlog`

### Phase 2 Exit Criteria
- [ ] All Phase 2 tests pass with real internet
- [ ] Demo 2 runs successfully end-to-end
- [ ] Demo 1 still passes (regression check)
- [ ] Crawl4AI extracts clean markdown from at least 3/5 URLs
- [ ] Deduplication works when running Demo 2 twice with same query

---

## Phase 3: Agents & Orchestration

**Goal**: All 5 agents implemented and wired into the Google ADK sequential/parallel orchestration. First complete research run.

### Tasks

| # | Task | File(s) | Description |
|---|------|---------|-------------|
| 3.1 | Query Planner agent | `sub_agents/query_planner.py` | Takes user query → outputs 3-5 sub-queries + research plan. Writes to `session.state["sub_queries"]` and `session.state["research_plan"]`. |
| 3.2 | Web Search agent | `sub_agents/web_search.py` | Reads `sub_queries` → runs DDG + Crawl4AI + Store for each. Writes `session.state["web_sources"]`. |
| 3.3 | Academic Search agent | `sub_agents/academic_search.py` | Reads `sub_queries` → runs Scholar + Crawl4AI + Store for each. Writes `session.state["academic_sources"]`. |
| 3.4 | Fact Verifier agent | `sub_agents/fact_verifier.py` | Reads stored chunks via Retrieve tool → cross-references claims → flags contradictions. Writes `session.state["verified_facts"]` and `session.state["contradictions"]`. |
| 3.5 | Report Writer agent | `sub_agents/report_writer.py` | Reads `research_plan` + `verified_facts` → generates section-by-section markdown report. Writes to file via file writer tool. |
| 3.6 | Root orchestrator | `agent.py` | `SequentialAgent(sub_agents=[planner, ParallelAgent([web_search, academic_search]), fact_verifier, report_writer])` |

### Tests (Phase 3)

**All agent tests use real Ollama + real internet.** These are slower but test actual agent behavior.

| Test File | What It Proves |
|-----------|---------------|
| `tests/unit/test_query_planner.py` | Planner generates valid sub-queries from a topic (real LLM) |
| `tests/unit/test_web_search_agent.py` | Web search agent populates session state with sources (real search + crawl) |
| `tests/unit/test_academic_search_agent.py` | Academic search agent finds papers (real Scholar API) |
| `tests/unit/test_fact_verifier.py` | Fact verifier identifies contradictions in test data (real LLM) |
| `tests/unit/test_report_writer.py` | Report writer produces structured markdown (real LLM) |
| `tests/integration/test_agent_orchestration.py` | Full sequential execution: planner → parallel research → verify → write |

### Demo 3: First Complete Research Run

```bash
python -m local_research_agent.demos.demo_full_pipeline "Battle of Thermopylae"
```

**What it does**:
1. Query Planner breaks down the topic (Ollama)
2. Web Search + Academic Search run in parallel (real internet)
3. Fact Verifier cross-references stored chunks (Ollama + ChromaDB)
4. Report Writer generates a markdown report (Ollama)
5. Report saved to `reports/`

**Success criteria**: A coherent markdown report is generated with sections, citations, and no obvious contradictions.

### Integration with Phase 1+2
- All agents use Phase 2 tools
- All tools use Phase 1 storage
- Session state flows through Google ADK's built-in state management
- Config and logging from Phase 1

### Phase 3 Exit Criteria
- [ ] All Phase 3 tests pass with real Ollama + real internet
- [ ] Demo 3 produces a readable report for at least 2 different queries
- [ ] Demo 1 and Demo 2 still pass (regression)
- [ ] Parallel agent execution doesn't cause state conflicts
- [ ] Graceful degradation when Academic Search returns no results

---

## Phase 4: CLI + Prompt Tuning

**Goal**: Polished CLI entry point. Prompts refined through iterative testing on diverse queries.

### Tasks

| # | Task | File(s) | Description |
|---|------|---------|-------------|
| 4.1 | CLI entry point | `__main__.py` or `cli.py` | `python -m local_research_agent "query"`. Arguments: `--model`, `--output-dir`, `--verbose`, `--no-scholar`, `--max-sources`. |
| 4.2 | Healthcheck module | `healthcheck.py` | Pre-flight: Ollama reachable? Model pulled? Disk space? Internet? Crawl4AI browser installed? |
| 4.3 | Prompt tuning — Query Planner | `sub_agents/query_planner.py` | Iterate on system prompt to produce better sub-queries. Test with 5+ diverse topics. |
| 4.4 | Prompt tuning — Fact Verifier | `sub_agents/fact_verifier.py` | Tune for fewer false positives on contradictions. Test with known-answer topics. |
| 4.5 | Prompt tuning — Report Writer | `sub_agents/report_writer.py` | Tune for better structure, citation integration, narrative flow. |
| 4.6 | Progress feedback | `agent.py` / callbacks | Print progress to console: "Planning...", "Searching (3/5 sub-queries)...", "Verifying facts...", "Writing report..." |

### Tests (Phase 4)

| Test File | What It Proves |
|-----------|---------------|
| `tests/unit/test_cli.py` | CLI argument parsing, error handling for missing Ollama |
| `tests/unit/test_healthcheck.py` | Health checks detect missing dependencies (real checks) |
| `tests/e2e/test_full_research.py` | Full E2E: historical query, scientific query, minimal-mode (no API keys) |

### Demo 4: Polished CLI

```bash
# Standard usage
python -m local_research_agent "Mongol Invasion of Japan in 1274"

# With options
python -m local_research_agent "CRISPR gene editing" --verbose --max-sources 20

# Health check
python -m local_research_agent --healthcheck
```

**Success criteria**: Clean CLI experience with progress feedback, healthcheck, and a high-quality report.

### Prompt Tuning Benchmark Queries
Run these 5 queries and evaluate report quality:

| Query | Tests |
|-------|-------|
| "Mongol Invasion of Japan in 1274" | Historical event, well-documented |
| "CRISPR gene editing mechanism" | Scientific topic, academic sources |
| "Fall of Constantinople 1453" | Historical, multi-perspective |
| "mRNA vaccine technology" | Science + recent events |
| "Causes of the 2008 financial crisis" | Complex multi-causal topic |

### Phase 4 Exit Criteria
- [ ] CLI works with `python -m local_research_agent "query"`
- [ ] Healthcheck catches missing Ollama, missing model, no internet
- [ ] All 5 benchmark queries produce acceptable reports
- [ ] E2E tests pass with real internet + real Ollama
- [ ] All previous demos still work (regression)

---

## Phase 5: Optimization & Hardening

**Goal**: Production-quality robustness. Performance profiling. Report quality evaluation framework.

### Tasks

| # | Task | File(s) | Description |
|---|------|---------|-------------|
| 5.1 | Performance profiling | `scripts/profile.py` | Measure per-phase timing, peak RAM, peak VRAM. Compare against budget in techContext.md. |
| 5.2 | Memory optimization | Various | Reduce peak memory: unload embedding model after use, limit ChromaDB cache, tune Crawl4AI concurrency. |
| 5.3 | Error recovery | `tools/*.py`, `sub_agents/*.py` | Retry logic for transient failures. Partial failure handling (3/5 URLs fail → continue with 2). |
| 5.4 | Rate limiting | `tools/ddg_search.py`, `tools/scholar_search.py` | Adaptive backoff for DDG. Respect Semantic Scholar 100 req/5min limit. |
| 5.5 | Report quality evaluator | `scripts/evaluate_report.py` | Automated checks: word count, section count, citation count, source diversity, self-contradiction detection. |
| 5.6 | Model benchmarking | `scripts/benchmark_models.py` | Compare Mistral 7B vs Qwen2.5 7B on the 5 benchmark queries. Measure quality + speed. |

### Tests (Phase 5)

| Test File | What It Proves |
|-----------|---------------|
| `tests/e2e/test_performance.py` | Peak RAM < 12GB, full run < 15 min |
| `tests/e2e/test_error_recovery.py` | System recovers from partial crawl failures, API timeouts |
| `tests/e2e/test_report_quality.py` | Reports meet quality thresholds (word count, citations, sections) |

### Demo 5: Metrics Dashboard

```bash
python -m local_research_agent "Fall of Constantinople 1453" --profile
```

**Output includes**:
- Per-phase timing breakdown
- Peak RAM / VRAM usage
- Sources found / crawled / stored
- Report quality metrics (word count, citations, sections, source diversity)

### Phase 5 Exit Criteria
- [ ] Peak RAM stays under 12 GB for all benchmark queries
- [ ] Full research cycle completes in under 15 minutes
- [ ] System recovers gracefully from partial failures
- [ ] Report quality metrics pass for 4/5 benchmark queries
- [ ] Model comparison data documented

---

## Cross-Phase Regression Strategy

After each phase, run **all** previous demos and tests to catch regressions:

```bash
# Run all tests
pytest tests/ -v

# Run all demos in sequence
python -m local_research_agent.demos.demo_storage "test query"
python -m local_research_agent.demos.demo_search_pipeline "test query"
python -m local_research_agent.demos.demo_full_pipeline "test query"
```

## Timeline Estimate

| Phase | Estimated Duration | Cumulative |
|-------|-------------------|------------|
| Phase 1: Foundation | 3-4 days | Week 1 |
| Phase 2: Search & Crawl | 4-5 days | Week 2 |
| Phase 3: Agents | 5-7 days | Week 3-4 |
| Phase 4: CLI + Prompts | 3-5 days | Week 4-5 |
| Phase 5: Optimization | 3-5 days | Week 5-6 |

**Total**: ~4-6 weeks for a working, tested, optimized system.

