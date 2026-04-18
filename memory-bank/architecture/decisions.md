# Architecture Decision Records (ADRs)

Formal record of key architectural decisions with context and trade-offs.

---

## ADR-001: Google ADK as Orchestration Framework

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Need a Python framework to orchestrate multiple LLM agents with tool use, sequential and parallel execution, and shared state.

**Decision**: Use Google ADK (Agent Development Kit).

**Alternatives Considered**:
| Framework | Pros | Cons | Why Not |
|-----------|------|------|---------|
| **LangGraph** | Mature, flexible graph-based workflows | Complex setup, heavy abstraction | Over-engineered for our linear pipeline |
| **CrewAI** | Good multi-agent support | Opinionated role system, less control | Less flexibility in tool/state design |
| **AutoGen** | Strong multi-agent chat | Chat-centric, not pipeline-centric | Wrong paradigm for sequential research |
| **Custom** | Full control | High maintenance burden | Reinventing well-solved problems |

**Consequences**:
- (+) Native `SequentialAgent` and `ParallelAgent` match our exact workflow
- (+) `LiteLlm` wrapper provides seamless Ollama integration
- (+) `session.state` is clean, simple state passing
- (+) `FunctionTool` auto-generates schemas from type hints
- (-) Tied to Google's design decisions and release cadence
- (-) Relatively newer framework — less community examples than LangChain

---

## ADR-002: Ollama for Local LLM Serving

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Need a local LLM server that works with 8GB VRAM and exposes an API compatible with Google ADK/LiteLLM.

**Decision**: Use Ollama with quantized 7B models.

**Alternatives Considered**:
| Tool | Pros | Cons | Why Not |
|------|------|------|---------|
| **llama.cpp server** | Lightweight, flexible | Manual setup, no model management | More operational overhead |
| **vLLM** | Fast inference, batching | High VRAM usage, server-oriented | Needs more VRAM than available |
| **text-generation-webui** | GUI, many model formats | Heavy, not API-first | Overkill for our use case |
| **LocalAI** | OpenAI-compatible drop-in | Less mature, more config | Ollama is simpler |

**Consequences**:
- (+) One-command model download (`ollama pull mistral`)
- (+) OpenAI-compatible API that LiteLLM speaks natively
- (+) Automatic GPU offloading, quantization-aware
- (+) Runs as a system service, model stays loaded in VRAM
- (-) Limited to models Ollama supports (but coverage is excellent)
- (-) Single-request serving (no batching like vLLM)

---

## ADR-003: Hybrid Storage (ChromaDB + SQLite)

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Need to store research content for semantic retrieval AND structured metadata queries.

**Decision**: ChromaDB (embedded) for vectors + SQLite for metadata.

**Alternatives Considered**:
| Approach | Pros | Cons | Why Not |
|----------|------|------|---------|
| **ChromaDB only** | Single system | No structured queries, limited metadata filtering | Can't query "all sources from domain X with reliability > 0.8" |
| **SQLite + FTS5** | Single file, full-text search | No semantic search | BM25 ≠ semantic similarity |
| **LanceDB** | Serverless, embedded | Less mature, smaller community | ChromaDB has better Python integration |
| **FAISS + SQLite** | Fast ANN search | No built-in metadata, manual glue code | ChromaDB provides metadata + vectors in one |
| **Qdrant** | Full-featured vector DB | Requires server process | Embedded mode less mature than ChromaDB |

**Consequences**:
- (+) Semantic search via ChromaDB for fact verification
- (+) Structured queries via SQLite for source management
- (+) Both are embedded/serverless — zero operational overhead
- (+) Both are well-tested, production-quality
- (-) Two systems to maintain and keep in sync
- (-) Join operations require application-level logic (not SQL joins)

---

## ADR-004: Crawl4AI for Content Extraction

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Need to extract clean, relevant content from web pages found by search tools.

**Decision**: Use Crawl4AI with BM25ContentFilter.

**Alternatives Considered**:
| Tool | Pros | Cons | Why Not |
|------|------|------|---------|
| **BeautifulSoup + requests** | Simple, lightweight | No JS rendering, no content filtering | Many sites need JS |
| **Trafilatura** | Good at article extraction | No JS support, no async | Missing JS + async |
| **Playwright direct** | Full browser control | No content extraction logic | Would need custom extraction |
| **Scrapy** | Mature crawling framework | Overkill, sync, no JS | Wrong tool for targeted extraction |

**Consequences**:
- (+) BM25 content filter extracts only relevant content (10x reduction in noise)
- (+) Async-native with `arun_many()` for concurrent crawling
- (+) Clean Markdown output — ideal for LLM input
- (+) Handles JS-heavy pages, popups, overlays
- (-) Chromium instances are memory-heavy (~300MB each)
- (-) Additional dependency (browser binary + Python package)

---

## ADR-005: CPU-Only Embeddings

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Need embeddings for ChromaDB semantic search. GPU is fully allocated to the LLM.

**Decision**: Use `all-MiniLM-L6-v2` via `sentence-transformers` on CPU.

**Alternatives Considered**:
| Model | Dims | Speed | Quality | Why Not |
|-------|------|-------|---------|---------|
| **BGE-small-en** | 384 | Fast | Slightly better | Marginal improvement not worth the change |
| **E5-small-v2** | 384 | Fast | Similar | Not significantly better |
| **nomic-embed-text** | 768 | Medium | Better | 2x storage, overkill for small corpus |
| **Ollama embeddings** | Varies | Slow (GPU) | Variable | Would contend with LLM for VRAM |

**Consequences**:
- (+) Zero GPU usage — all VRAM for the LLM
- (+) ~100MB RAM, ~100 chunks/sec — fast enough
- (+) Well-benchmarked on MTEB retrieval tasks
- (-) 256-token max input (chunks beyond this are truncated)
- (-) 384-dim vectors are lower quality than 768/1024-dim alternatives
- (-) Acceptable trade-off: we're searching a small, focused corpus (100-300 chunks per session)

---

## ADR-006: Sequential-First Orchestration

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: How should agents be orchestrated? Options range from fully parallel to fully sequential.

**Decision**: Sequential root agent with parallel research sub-phase.

**Rationale**:
1. **Correctness**: Research must complete before verification; verification before writing
2. **Resource management**: LLM inference (VRAM) and crawling (RAM) don't compete
3. **Debuggability**: Linear execution is easier to trace and debug
4. **Data dependencies**: Each phase depends on the previous phase's output

**Parallelism is used only where it's safe**: Web search and academic search are independent — they can run concurrently without state conflicts.

**Consequences**:
- (+) Predictable execution order
- (+) No resource contention between phases
- (+) Easy to debug with structured logging
- (-) Total time = sum of all phases (no overlap between plan/research/verify/write)
- (-) Acceptable: total time is 2-5 minutes, which is reasonable for deep research

---

## ADR-007: Session-Per-Collection Storage Model

**Status**: Accepted  
**Date**: 2026-04-11  
**Context**: Should ChromaDB use one global collection or per-session collections?

**Decision**: One ChromaDB collection per research session.

**Rationale**:
- **Isolation**: No cross-contamination between unrelated research topics
- **Cleanup**: Easy to delete a session's data (drop collection)
- **Performance**: Smaller collections = faster search
- **Simplicity**: No need for session_id filtering in every query

**Trade-off**: Cannot cross-reference data across sessions. A future "Mongol history" query can't reuse data from a prior "Mongol invasion of Japan" session.

**Future consideration**: A v2 "knowledge base mode" could use a single shared collection with session_id metadata filtering.

