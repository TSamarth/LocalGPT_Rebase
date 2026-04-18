# Active Context: LocalGPT Deep Research Agent

## Current Work Focus
**Phase 1: Documentation & Architecture Design**

We are building comprehensive documentation before any code implementation. The goal is to have a complete, detailed specification that covers:
- Architecture and system design (✅ documented in systemPatterns.md)
- Technology stack and constraints (✅ documented in techContext.md)
- Agent specifications and prompt design (→ in specs/)
- Storage layer design (→ in specs/)
- Testing strategy (→ in specs/)
- Coding practices and conventions (→ in specs/)

## Recent Changes
1. **Initial project brief established** — Defined core requirements, success criteria, constraints, and project phases.
2. **Architecture designed** — Sequential-Parallel hybrid orchestration with 4 specialized agents (Query Planner, Web Search, Academic Search, Fact Verifier, Report Writer).
3. **Crawl4AI integration designed** — Slots in as a second-stage tool after search, uses BM25ContentFilter for relevance filtering, `arun_many()` for concurrent crawling.
4. **Hybrid storage layer designed** — ChromaDB (embedded) for vector search + SQLite for structured metadata. `all-MiniLM-L6-v2` for CPU-only embeddings.
5. **Hardware budget validated** — Peak usage: ~2.5 GB RAM + ~7 GB VRAM, well within limits.
6. **Phased implementation plan created** — 6 phases (docs → foundation → tools → agents → CLI → optimization), each ending with a demo that integrates with all prior phases. See `specs/implementation-phases.md`.
7. **Testing strategy overhauled** — All tests use real internet, real APIs, real storage. No mocking of network calls. `pytest-retry` for flakiness. Tests emulate real usage.
8. **Embedding model made configurable** — `config.py` exposes `EMBEDDING_MODEL` env var, default `all-MiniLM-L6-v2`.

## Active Decisions & Considerations

### Decided
- **Framework**: Google ADK with LiteLlm → Ollama
- **LLM**: Start with Mistral 7B Q4_K_M (benchmark Qwen2.5 7B later)
- **Crawling**: Crawl4AI with BM25 content filtering
- **Storage**: ChromaDB (vectors) + SQLite (metadata) — hybrid approach
- **Embeddings**: all-MiniLM-L6-v2 on CPU
- **Chunking**: 512 tokens, 50-token overlap, paragraph-boundary-aware
- **Concurrency**: max_concurrent=3 for Crawl4AI browser instances
- **Degradation**: SerpAPI optional; system fully functional with free APIs only
- **Testing**: No mocks — all tests hit real APIs, real internet, real storage. pytest-retry for flakiness.
- **Embedding configurability**: `EMBEDDING_MODEL` env var in config, default `all-MiniLM-L6-v2`

### Open Questions
1. **Model benchmarking**: Mistral 7B vs Qwen2.5 7B — which produces better research synthesis? Need empirical testing.
2. **Session persistence**: Should ChromaDB collections persist across research sessions for incremental research, or start fresh each time? Leaning toward persistent with deduplication.
3. **Report length management**: For complex topics, reports could exceed the LLM's context window during generation. Need a section-by-section generation strategy.
4. **Rate limiting strategy**: DuckDuckGo doesn't publish rate limits. Need to test what's sustainable without getting blocked.
5. **Error recovery**: If a research phase partially fails (e.g., 3/5 URLs crawled), should we retry, skip, or alert the user?

## Important Patterns & Preferences
- **Documentation-first approach**: Complete specs before implementation
- **Memory bank maintained**: All decisions and changes documented here
- **CLI-first**: No GUI in v1; focus on core research pipeline quality
- **English-only reports**: v1 scope limitation
- **Structured logging**: Using `structlog` for consistent, parseable logs
- **Tools never throw**: All tool functions return `{"status": "success/error", "data/message": ...}`

## Next Steps
1. ✅ Create core memory bank files (projectbrief, productContext, systemPatterns, techContext)
2. ✅ Create detailed specification documents:
   - Agent specifications (prompts, inputs/outputs for each agent)
   - Storage layer design (schema, operations, lifecycle)
   - Crawl4AI integration spec
   - Testing strategy
   - Coding conventions
   - Limitations & constraints
   - Architecture Decision Records (ADRs)
3. ✅ Create progress.md with implementation roadmap
4. ✅ Create project README
5. ⬜ Begin Phase 2 implementation (storage layer + tools)

## Learnings & Project Insights
- 7B models at Q4_K_M quantization are the sweet spot for 8GB VRAM — they leave ~1.5 GB for KV cache which supports reasonable context windows
- Crawl4AI's BM25ContentFilter is critical for research quality — without it, crawled pages include nav bars, ads, and irrelevant content that wastes LLM context
- Sequential orchestration between crawling and LLM phases is a natural fit here — it's not just an architectural choice, it's a resource management strategy
- ChromaDB embedded mode adds zero operational overhead vs. client-server mode — important for a "just works" local tool


