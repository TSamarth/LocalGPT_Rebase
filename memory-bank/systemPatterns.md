# System Patterns: LocalGPT Deep Research Agent

## System Architecture

### High-Level Architecture Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                     Google ADK Orchestration Layer                   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Root Agent (SequentialAgent)                     │   │
│  │                                                              │   │
│  │  ┌────────────────┐                                          │   │
│  │  │ Query Planner  │  Step 1: Decompose query into            │   │
│  │  │  (LlmAgent)    │  sub-questions + research plan           │   │
│  │  └───────┬────────┘                                          │   │
│  │          ▼                                                    │   │
│  │  ┌────────────────────────────────────────────┐              │   │
│  │  │       Research Phase (ParallelAgent)        │              │   │
│  │  │                                            │              │   │
│  │  │  ┌──────────────┐  ┌───────────────────┐   │              │   │
│  │  │  │ Web Search   │  │ Academic Search   │   │              │   │
│  │  │  │  Agent       │  │  Agent            │   │              │   │
│  │  │  │ (LlmAgent)   │  │ (LlmAgent)        │   │              │   │
│  │  │  │              │  │                   │   │              │   │
│  │  │  │ Tools:       │  │ Tools:            │   │              │   │
│  │  │  │ - DDG Search │  │ - SerpAPI Search  │   │              │   │
│  │  │  │ - Crawl4AI   │  │ - Scholar Search  │   │              │   │
│  │  │  │ - Store      │  │ - Crawl4AI        │   │              │   │
│  │  │  └──────────────┘  │ - Store           │   │              │   │
│  │  │                    └───────────────────┘   │              │   │
│  │  └────────────────────────────────────────────┘              │   │
│  │          ▼                                                    │   │
│  │  ┌────────────────┐                                          │   │
│  │  │ Fact Verifier  │  Step 3: Cross-reference claims,         │   │
│  │  │  (LlmAgent)    │  flag contradictions, verify facts       │   │
│  │  │                │  Tools: Retrieve (semantic search)       │   │
│  │  └───────┬────────┘                                          │   │
│  │          ▼                                                    │   │
│  │  ┌────────────────┐                                          │   │
│  │  │ Report Writer  │  Step 4: Synthesize verified facts       │   │
│  │  │  (LlmAgent)    │  into structured Markdown report        │   │
│  │  │                │  Tools: SaveReport                      │   │
│  │  └────────────────┘                                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        Tools Layer                                  │
│                                                                     │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐ ┌────────────┐  │
│  │ DDG Search   │ │ SerpAPI     │ │ Scholar      │ │ Crawl4AI   │  │
│  │ Tool         │ │ Search Tool │ │ Search Tool  │ │ Crawl Tool │  │
│  └─────────────┘ └─────────────┘ └──────────────┘ └────────────┘  │
│                                                                     │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐                  │
│  │ Store Tool   │ │ Retrieve    │ │ Save Report  │                  │
│  │              │ │ Tool        │ │ Tool         │                  │
│  └─────────────┘ └─────────────┘ └──────────────┘                  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                     Storage Layer                                   │
│                                                                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐    │
│  │   ChromaDB            │  │   SQLite                         │    │
│  │   (Vector Store)      │  │   (Metadata Store)               │    │
│  │                       │  │                                  │    │
│  │   - Content chunks    │  │   - sources (url, domain, type,  │    │
│  │   - Embeddings        │  │     crawl_time, reliability)     │    │
│  │   - Semantic search   │  │   - research_sessions            │    │
│  │                       │  │   - query_plans                  │    │
│  └──────────────────────┘  └──────────────────────────────────┘    │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                     Infrastructure Layer                            │
│                                                                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐    │
│  │   Ollama              │  │   Sentence Transformers          │    │
│  │   (LLM Server)        │  │   (Embedding Model)             │    │
│  │                       │  │                                  │    │
│  │   - Mistral 7B Q4     │  │   - all-MiniLM-L6-v2            │    │
│  │   - localhost:11434   │  │   - CPU-only, ~100MB RAM         │    │
│  │   - ~5-6GB VRAM      │  │   - 384-dim vectors              │    │
│  └──────────────────────┘  └──────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Design Patterns

### 1. Sequential-Parallel Hybrid Orchestration
The root agent is a `SequentialAgent` that enforces strict phase ordering:
```
Plan → Research (parallel) → Verify → Write
```

This ensures:
- Research doesn't start before the plan is ready
- Verification doesn't start before all research is collected
- The report doesn't get written with unverified data
- LLM inference and web crawling don't compete for resources simultaneously

The research phase internally uses a `ParallelAgent` to run web and academic search concurrently, maximizing throughput during the I/O-heavy search phase.

### 2. State-Passing via ADK Session State
Agents communicate through ADK's `session.state` dictionary — a shared key-value store scoped to the session:

| Key | Written By | Read By | Type | Description |
|-----|-----------|---------|------|-------------|
| `user_query` | Runner | Query Planner | `str` | Original user query |
| `sub_queries` | Query Planner | Web Search, Academic Search | `list[str]` | Decomposed sub-questions |
| `research_plan` | Query Planner | Report Writer | `str` | Structured research plan / outline |
| `web_results` | Web Search Agent | Fact Verifier | `list[dict]` | Summary of web sources found |
| `academic_results` | Academic Search Agent | Fact Verifier | `list[dict]` | Summary of academic sources found |
| `session_id` | Runner | All agents/tools | `str` | Unique session identifier for storage |
| `verified_facts` | Fact Verifier | Report Writer | `list[dict]` | Cross-verified facts with citations |
| `contradictions` | Fact Verifier | Report Writer | `list[dict]` | Flagged contradictions (excluded or noted) |
| `report_path` | Report Writer | Runner | `str` | Path to the generated report file |

### 3. Tool Design Pattern
All tools follow a consistent pattern:
- **Pure functions** wrapped as Google ADK `FunctionTool` instances
- **Typed parameters** with clear docstrings (ADK uses these for the LLM's tool schema)
- **Error handling**: Tools never throw — they return structured error messages that the agent can reason about
- **Logging**: All tool invocations log inputs and outputs for debugging
- **Session-aware**: Tools that interact with storage receive the `session_id` to scope their operations

```python
# Pattern for all tools:
from google.adk.tools import FunctionTool

def my_tool(param1: str, param2: int) -> dict:
    """Clear docstring that the LLM reads to understand when/how to use the tool.
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        dict with 'status' ('success' or 'error') and 'data' or 'message'
    """
    try:
        # ... implementation ...
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"my_tool failed: {e}")
        return {"status": "error", "message": str(e)}

my_tool_instance = FunctionTool(my_tool)
```

### 4. Graceful Degradation Pattern
The system degrades gracefully based on available API keys and services:

```
Full Mode (all keys configured):
  DDG + SerpAPI + Semantic Scholar + Crawl4AI → 4 source types

Standard Mode (no API keys):
  DDG + Semantic Scholar + Crawl4AI → 3 source types (free APIs only)

Minimal Mode (no internet):
  Previously cached data in ChromaDB → retrieval-only research
```

Each search tool checks for its API key at invocation time and returns a clear message if unavailable, allowing the agent to continue with remaining tools.

## Key Technical Decisions

### Decision 1: Google ADK as Orchestration Framework
**Choice**: Google ADK (Agent Development Kit)
**Alternatives Considered**: LangGraph, CrewAI, AutoGen, custom orchestration
**Rationale**:
- Native `SequentialAgent` and `ParallelAgent` primitives match our exact workflow
- `LiteLlm` wrapper provides seamless Ollama integration
- `session.state` is a clean state-passing mechanism
- First-class `FunctionTool` support with automatic schema generation from type hints
- Active development and Google backing

### Decision 2: Ollama + 7B Quantized Model
**Choice**: Ollama serving Mistral 7B (Q4_K_M quantization) or Qwen2.5 7B Instruct (Q4_K_M)
**Alternatives Considered**: llama.cpp direct, vLLM, text-generation-webui
**Rationale**:
- Ollama exposes an OpenAI-compatible API that LiteLlm speaks natively
- Q4_K_M quantization fits in 8GB VRAM with room for KV cache
- Simple installation and model management (`ollama pull model_name`)
- Automatic GPU offloading
**Trade-off**: 4-bit quantization sacrifices some quality vs. FP16, but 7B models at Q4_K_M are well-tested and performant

### Decision 3: Crawl4AI for Web Content Extraction
**Choice**: Crawl4AI with `AsyncWebCrawler`
**Alternatives Considered**: BeautifulSoup + requests, Scrapy, Playwright direct, Trafilatura
**Rationale**:
- Async-native with `arun_many()` for concurrent crawling
- Built-in `BM25ContentFilter` for relevance-based content filtering — critical for extracting only query-relevant content from pages
- Clean Markdown output — ideal input format for LLM reasoning
- Handles JavaScript-heavy pages via headless Chromium
- Manages browser lifecycle, sessions, and anti-detection
**Trade-off**: Chromium instances are memory-heavy (~150-300MB each). Mitigated by limiting `max_concurrent=3`

### Decision 4: Hybrid Storage (ChromaDB + SQLite)
**Choice**: ChromaDB (embedded) for vectors + SQLite for metadata
**Alternatives Considered**: Pure ChromaDB, LanceDB, Qdrant, pure SQLite with FTS5, FAISS + SQLite
**Rationale**:
- **ChromaDB embedded mode**: Zero server overhead, Python-native, persistent storage, good enough performance for our scale (<100K chunks per session)
- **SQLite**: Battle-tested, zero-config, excellent for structured queries (source reliability, domain filtering, timestamp ranges)
- **Hybrid advantage**: Semantic search (ChromaDB) + structured queries (SQLite) + cross-reference joins
**Trade-off**: Two storage systems to maintain. Mitigated by thin wrapper classes that abstract the complexity.

### Decision 5: all-MiniLM-L6-v2 for Embeddings
**Choice**: `sentence-transformers/all-MiniLM-L6-v2`
**Alternatives Considered**: Ollama embedding models, BGE-small, E5-small, nomic-embed-text
**Rationale**:
- Runs on CPU only — leaves all GPU VRAM for the LLM
- ~22M parameters, ~100MB RAM footprint
- 384-dimensional vectors — small storage footprint in ChromaDB
- Well-benchmarked on retrieval tasks (MTEB)
- Native `sentence-transformers` integration with ChromaDB
**Trade-off**: Lower retrieval quality than larger embedding models (768-dim or 1024-dim). Acceptable for our use case where we're retrieving from a small, focused corpus per research session.

### Decision 6: Text Chunking Strategy
**Choice**: 512-token chunks with 50-token overlap, paragraph-boundary-aware splitting
**Rationale**:
- 512 tokens balances retrieval precision (not too large → diluted relevance) with context completeness (not too small → lost coherence)
- 50-token overlap ensures sentences split across chunk boundaries are recoverable
- Paragraph-boundary splitting preserves natural semantic units
- At ~512 tokens/chunk, a typical 5000-word article produces ~20 chunks — manageable for ChromaDB

## Component Relationships

### Data Flow
```
User Query
    │
    ▼
Query Planner ──writes──► session.state["sub_queries"]
    │                      session.state["research_plan"]
    ▼
┌─────────────────────────────────┐
│ ParallelAgent                   │
│                                 │
│ Web Search Agent                │
│   DDG Search ► URLs             │
│   Crawl4AI  ► Markdown content  │──writes──► ChromaDB + SQLite
│   Store     ► Chunks + embeds   │            session.state["web_results"]
│                                 │
│ Academic Search Agent           │
│   Scholar   ► URLs              │
│   Crawl4AI  ► Markdown content  │──writes──► ChromaDB + SQLite
│   Store     ► Chunks + embeds   │            session.state["academic_results"]
└─────────────────────────────────┘
    │
    ▼
Fact Verifier
    Retrieve  ◄──reads──── ChromaDB (semantic search)
              ◄──reads──── SQLite (source metadata)
    ──writes──► session.state["verified_facts"]
               session.state["contradictions"]
    │
    ▼
Report Writer
    ◄──reads──── session.state["verified_facts"]
                 session.state["research_plan"]
    SaveReport ──writes──► reports/{session_id}.md
    ──writes──► session.state["report_path"]
```

### Dependency Graph (Python Modules)
```
agent.py (root)
├── sub_agents/query_planner.py
├── sub_agents/web_search.py
│   ├── tools/ddg_search.py
│   ├── tools/crawl_tool.py
│   └── tools/store_tool.py
├── sub_agents/academic_search.py
│   ├── tools/serp_search.py
│   ├── tools/scholar_search.py
│   ├── tools/crawl_tool.py  (shared)
│   └── tools/store_tool.py  (shared)
├── sub_agents/fact_verifier.py
│   └── tools/retrieve_tool.py
├── sub_agents/report_writer.py
│   └── tools/file_writer.py
└── storage/
    ├── vector_store.py  (ChromaDB)
    ├── metadata_store.py (SQLite)
    └── chunker.py
```

## Critical Implementation Paths

### Path 1: Search → Crawl → Store Pipeline
This is the most complex data path and must handle:
1. Search tool returns N URLs (cap at 5-8 per sub-query to manage crawl time)
2. Crawl tool receives URLs, applies BM25 content filter with sub-query context
3. Crawl results are chunked (512 tokens, 50 overlap)
4. Chunks are embedded via all-MiniLM-L6-v2
5. Chunks + embeddings inserted into ChromaDB with metadata
6. Source metadata (URL, domain, timestamp, source_type) inserted into SQLite
7. Summary returned to agent for session state update

**Failure modes**: URL timeout, bot detection, empty content, embedding failure
**Mitigations**: Try/catch per URL, skip failures, log warnings, continue with remaining URLs

### Path 2: Fact Verification Pipeline
This is the most quality-critical path:
1. Fact Verifier agent receives summary of all sources from session state
2. For each major claim/topic, it formulates a semantic search query
3. Retrieve tool searches ChromaDB for top-k (k=5) relevant chunks
4. Agent compares chunks from different sources for consistency
5. Contradictions are flagged with source details
6. Verified facts are annotated with supporting source URLs

**Failure modes**: Insufficient sources for verification, ambiguous claims, LLM hallucination during verification
**Mitigations**: Minimum source threshold (2+ sources per key claim), explicit "insufficient evidence" marking, structured output format

### Path 3: Resource Management
Because LLM inference and Crawl4AI both consume significant resources:
1. Sequential orchestration ensures crawling (RAM-heavy) and LLM inference (VRAM-heavy) don't compete
2. Crawl4AI limited to `max_concurrent=3` browser instances
3. ChromaDB embedding runs on CPU (no GPU contention)
4. Ollama server runs persistently; model stays loaded in VRAM between agent calls

## Design Patterns in Use

| Pattern | Where Used | Purpose |
|---------|-----------|---------|
| **Pipeline** | Root SequentialAgent | Enforce phase ordering |
| **Fan-out/Fan-in** | Research ParallelAgent | Concurrent search from multiple sources |
| **Repository** | storage/ module | Abstract storage implementation from agents |
| **Strategy** | Search tools | Swap search backends (DDG vs SERP) without changing agent logic |
| **Circuit Breaker** | Crawl tool | Skip failed URLs after timeout, don't retry endlessly |
| **Event Sourcing** | SQLite metadata | Full audit trail of what was crawled, when, and why |
| **Decorator** | Tool wrapper functions | Consistent logging, error handling, timing |

