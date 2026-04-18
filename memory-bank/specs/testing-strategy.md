# Testing Strategy

Comprehensive testing approach for the LocalGPT Deep Research Agent.

**Core Principle**: All tests emulate real usage. No mocking of network calls, external APIs, or LLM inference. Every test hits real services (internet, Ollama, ChromaDB, Crawl4AI) to validate actual behavior under real conditions.

---

## Testing Pyramid

```
        ┌─────────┐
        │  E2E    │   3-5 tests: Full research pipeline
        │  Tests  │   Slow, require Ollama + Internet
        ├─────────┤
        │ Integr. │   10-15 tests: Component interactions
        │  Tests  │   Require Internet, real storage
        ├─────────┤
        │  Unit   │   40-60 tests: Individual functions
        │  Tests  │   Real APIs, real embeddings, real storage
        └─────────┘
```

> **Why no mocks?** Mocking network calls hides real-world issues: API response format changes, rate limiting, timeout behavior, content quality variation. Since this is a research tool that depends heavily on external data quality, tests must validate against real data to be meaningful.

---

## Test Requirements

All tests require:
1. **Internet connection** — for DDG, Semantic Scholar, Crawl4AI
2. **Real storage** — ChromaDB and SQLite in temp directories (cleaned up after)
3. **Real embeddings** — `all-MiniLM-L6-v2` (configurable via `config.py`, fast on CPU)

Agent-level and E2E tests additionally require:
4. **Ollama running** with the configured model pulled

### Handling Flakiness

Since real APIs can be intermittently unavailable:
- Use **`pytest-retry`** with `max_retries=2` and `retry_delay=5` for all tests hitting external APIs
- Tests should use **well-known, stable queries** (e.g., "Mongol invasions of Japan", "CRISPR") that reliably return results
- Tests assert **structural correctness** (result has expected keys, non-empty content) rather than exact content matching
- Mark tests with `@pytest.mark.online` for documentation (but all tests require internet)

---

## Unit Tests

### Scope
Test individual functions and classes with real dependencies. Storage uses temp directories. Searches hit real APIs. Embeddings use the real model.

### Target Coverage: >80%

### Test Categories

#### 1. Chunker Tests (`test_chunker.py`)
```python
class TestTextChunker:
    def test_basic_chunking(self):
        """Text is split into expected number of chunks."""
    
    def test_overlap_applied(self):
        """Overlap tokens appear at start of next chunk."""
    
    def test_paragraph_boundaries(self):
        """Chunks split at paragraph boundaries when possible."""
    
    def test_empty_text(self):
        """Empty input returns empty list."""
    
    def test_short_text_single_chunk(self):
        """Text shorter than chunk_size returns one chunk."""
    
    def test_markdown_headers_as_boundaries(self):
        """Markdown headers are preferred split points."""
    
    def test_urls_not_split(self):
        """URLs are kept intact within chunks."""
    
    def test_chunk_metadata(self):
        """Each chunk has correct char_start, char_end, token_count."""
    
    def test_very_long_paragraph(self):
        """Falls back to sentence splitting for long paragraphs."""
    
    def test_unicode_content(self):
        """Handles non-ASCII content correctly."""
```

#### 2. Vector Store Tests (`test_vector_store.py`)

Uses real ChromaDB (temp dir) and real `all-MiniLM-L6-v2` embeddings.

```python
class TestVectorStore:
    def test_create_collection(self):
        """Creates a new ChromaDB collection."""
    
    def test_add_documents(self):
        """Adds documents with real embeddings and metadata."""
    
    def test_query_returns_relevant(self):
        """Semantic query returns most similar documents (real embeddings)."""
    
    def test_query_with_filter(self):
        """Filtered query respects metadata constraints."""
    
    def test_duplicate_id_handling(self):
        """Adding duplicate IDs updates rather than duplicates."""
    
    def test_empty_collection_query(self):
        """Querying empty collection returns empty results."""
    
    def test_collection_persistence(self):
        """Data survives client restart (persistent mode)."""
    
    def test_configurable_embedding_model(self):
        """VectorStore uses embedding model from config (default all-MiniLM-L6-v2)."""
```

#### 3. Metadata Store Tests (`test_metadata_store.py`)

Uses real SQLite (temp file).

```python
class TestMetadataStore:
    def test_create_session(self):
        """Creates a session row in SQLite."""
    
    def test_add_source(self):
        """Adds a source with all required fields."""
    
    def test_duplicate_url_rejected(self):
        """UNIQUE constraint prevents duplicate (session_id, url)."""
    
    def test_get_session_sources(self):
        """Returns all sources for a session."""
    
    def test_update_session_status(self):
        """Status transitions (in_progress → completed)."""
    
    def test_reliability_score(self):
        """Correct reliability score for known domains."""
    
    def test_content_hash_dedup(self):
        """check_duplicate returns True for existing hash."""
```

#### 4. Search Tool Tests (`test_ddg_search.py`, `test_serp_search.py`, `test_scholar_search.py`)

All search tests hit real APIs. Use stable, well-known queries.

```python
class TestDDGSearch:
    @pytest.mark.online
    def test_returns_results(self):
        """Real DDG search for 'Mongol invasions Japan' returns results."""
    
    def test_empty_query_handling(self):
        """Empty query returns error status."""
    
    @pytest.mark.online
    def test_result_structure(self):
        """Each result has title, url, snippet keys."""
    
    @pytest.mark.online
    def test_max_results_respected(self):
        """Returns at most N results as configured."""

class TestSerpSearch:
    def test_missing_api_key(self):
        """Returns graceful error when SERP_API_KEY not set."""
    
    @pytest.mark.online
    def test_returns_results_with_key(self):
        """Real SerpAPI returns results when key is available (skip if no key)."""

class TestScholarSearch:
    @pytest.mark.online
    def test_returns_papers(self):
        """Real Semantic Scholar API returns paper metadata for 'CRISPR'."""
    
    @pytest.mark.online
    def test_result_includes_citations(self):
        """Results include citation count for quality assessment."""
    
    @pytest.mark.online
    def test_rate_limit_handling(self):
        """Handles 429 response gracefully (may need rapid sequential calls to trigger)."""
```

#### 5. Crawl Tool Tests (`test_crawl_tool.py`)

Uses real Crawl4AI against known, stable URLs.

```python
class TestCrawlTool:
    @pytest.mark.online
    async def test_successful_crawl(self):
        """Crawls a known URL (e.g., Wikipedia page) and returns markdown content."""
    
    @pytest.mark.online
    async def test_bm25_filter_applied(self):
        """BM25ContentFilter reduces content to query-relevant sections."""
    
    @pytest.mark.online
    async def test_multiple_urls(self):
        """arun_many crawls multiple URLs concurrently."""
    
    @pytest.mark.online
    async def test_invalid_url_handling(self):
        """Invalid URL returns error in results, doesn't crash."""
    
    @pytest.mark.online
    async def test_content_truncation(self):
        """Very long content is truncated to configured limit."""
    
    @pytest.mark.online
    async def test_empty_content_filtered(self):
        """URLs returning empty content are treated as failures."""
```

#### 6. Store & Retrieve Tool Tests

Uses real storage (temp dirs) and real embeddings.

```python
class TestStoreTool:
    def test_store_chunks_and_embeds(self):
        """Content is chunked, embedded with real model, and stored."""
    
    def test_deduplication(self):
        """Duplicate content (by hash) is skipped on second store."""
    
    def test_source_metadata_stored(self):
        """URL, domain, source_type stored in SQLite."""

class TestRetrieveTool:
    def test_semantic_retrieval(self):
        """Returns relevant chunks for a query (real embeddings)."""
    
    def test_source_type_filter(self):
        """Filter by academic/web source type works."""
    
    def test_reliability_filter(self):
        """Min reliability filter excludes low-quality sources."""
    
    def test_returns_source_metadata(self):
        """Results include URL, domain, source_type."""
```

---

## Integration Tests

### Scope
Test interactions between components. All dependencies are real — real internet, real storage, real embeddings.

### Test Categories

#### 1. Search → Crawl → Store Pipeline (`test_search_crawl_pipeline.py`)
```python
class TestSearchCrawlStorePipeline:
    @pytest.mark.online
    async def test_ddg_to_crawl_to_store(self):
        """
        Real DDG search → Real Crawl4AI extraction →
        Real ChromaDB + SQLite storage.
        Verify: chunks in ChromaDB, source row in SQLite.
        """
    
    @pytest.mark.online
    async def test_partial_crawl_failure(self):
        """Pipeline continues when some URLs fail to crawl."""
    
    @pytest.mark.online
    async def test_duplicate_url_skipped(self):
        """Second store of same URL is deduplicated."""
```

#### 2. Storage Round-Trip (`test_storage_pipeline.py`)
```python
class TestStorageRoundTrip:
    def test_store_then_retrieve(self):
        """
        Store content with real embeddings → retrieve by semantic query →
        verify returned content matches stored content.
        """
    
    def test_multi_source_retrieval(self):
        """
        Store from 3 different URLs → retrieve →
        results come from multiple sources.
        """
    
    def test_filtered_retrieval(self):
        """
        Store web + academic sources →
        retrieve with source_type="academic" →
        only academic results returned.
        """
    
    def test_session_isolation(self):
        """
        Store in session A and session B →
        retrieve in session A → no session B results.
        """
```

#### 3. Agent Orchestration (`test_agent_orchestration.py`)

Requires Ollama running + internet.

```python
class TestAgentOrchestration:
    @pytest.mark.online
    async def test_sequential_execution_order(self):
        """
        Verify agents execute in order:
        planner → research → verifier → writer.
        Uses real Ollama for agent decisions.
        """
    
    @pytest.mark.online
    async def test_session_state_passing(self):
        """
        Query planner writes sub_queries →
        web search agent reads sub_queries →
        verify state keys are correctly passed.
        All with real LLM + real search.
        """
    
    @pytest.mark.online
    async def test_parallel_research_agents(self):
        """
        Web search and academic search run concurrently.
        Both write to session state without conflicts.
        Real searches, real Ollama.
        """
    
    @pytest.mark.online
    async def test_graceful_degradation_no_academic_results(self):
        """
        If academic search returns no results for a query,
        web search results still reach the fact verifier.
        """
```

---

## End-to-End Tests

### Scope
Full pipeline from user query to generated report. Requires Ollama + internet. Validates the entire system under real conditions.

### Tests (`test_full_research.py`)
```python
class TestFullResearch:
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_historical_query(self):
        """
        Query: "Mongol Invasion of Japan in 1274"
        
        Assertions:
        1. Report file is created at expected path
        2. Report contains expected sections (Background, Events, Aftermath)
        3. Report contains at least 1500 words
        4. Report contains source citations (URLs)
        5. Session marked as 'completed' in SQLite
        6. ChromaDB collection exists with stored chunks
        7. No contradictions are left unresolved in the report
        """
    
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_scientific_query(self):
        """
        Query: "CRISPR gene editing mechanism and applications"
        Tests academic search path more heavily.
        """
    
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_minimal_mode_no_api_keys(self):
        """
        Run with no optional API keys configured.
        Verify system works with DDG + Semantic Scholar only.
        """
    
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_complex_multicausal_query(self):
        """
        Query: "Causes of the 2008 financial crisis"
        Tests handling of complex, multi-perspective topics.
        """
```

### Performance & Robustness Tests (`test_performance.py`, `test_error_recovery.py`)
```python
class TestPerformance:
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_peak_ram_under_limit(self):
        """Full run stays under 12 GB peak RAM."""
    
    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.online
    async def test_completes_within_timeout(self):
        """Full run completes in under 15 minutes."""

class TestErrorRecovery:
    @pytest.mark.e2e
    @pytest.mark.online
    async def test_partial_crawl_failure_produces_report(self):
        """System produces a report even when some URLs fail to crawl."""
    
    @pytest.mark.e2e
    @pytest.mark.online
    async def test_no_academic_results_produces_report(self):
        """System produces a report using only web sources when Scholar returns nothing."""
```

---

## Test Configuration

### pyproject.toml
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "e2e: End-to-end tests (require Ollama + internet)",
    "slow: Tests that take >30 seconds",
    "integration: Integration tests (require internet + real storage)",
    "online: Tests that require internet access (all tests)",
]
testpaths = ["tests"]
```

### Additional Dependencies
```
pytest-retry          # Auto-retry for flaky API calls
pytest-timeout        # Prevent hung tests
pytest-asyncio        # Async test support
```

### Running Tests
```bash
# Unit tests (real APIs, real storage, real embeddings)
pytest tests/unit/ -v

# Integration tests (real everything)
pytest tests/integration/ -v

# E2E tests (slow, needs Ollama + internet)
pytest tests/e2e/ -v -m e2e

# All tests
pytest -v

# With coverage
pytest --cov=local_research_agent --cov-report=html

# With timeout (prevent hung tests)
pytest -v --timeout=300
```

### Shared Fixtures (`conftest.py`)
```python
import pytest
import tempfile
import chromadb

@pytest.fixture
def temp_dir():
    """Temporary directory for test data, cleaned up after."""
    with tempfile.TemporaryDirectory() as d:
        yield d

@pytest.fixture
def chroma_client(temp_dir):
    """Real ChromaDB client in temp directory."""
    return chromadb.PersistentClient(path=f"{temp_dir}/chroma")

@pytest.fixture
def sqlite_path(temp_dir):
    """Temporary SQLite database path."""
    return f"{temp_dir}/test_research.db"

@pytest.fixture
def sample_content():
    """Sample markdown content for testing."""
    return """
    ## Mongol Invasions of Japan
    
    The Mongol invasions of Japan took place in 1274 and 1281...
    
    ### Background
    Kublai Khan, the founder of the Yuan dynasty...
    
    ### The First Invasion (1274)
    The first invasion force consisted of...
    """

@pytest.fixture
def stable_search_query():
    """A query that reliably returns results from all search APIs."""
    return "Mongol invasions of Japan"

@pytest.fixture
def stable_crawl_url():
    """A URL that is reliably crawlable and returns substantial content."""
    return "https://en.wikipedia.org/wiki/Mongol_invasions_of_Japan"
```

---

## What We Do NOT Mock

| Dependency | Why Not Mocked |
|-----------|---------------|
| DuckDuckGo API | Must validate real result structure and content quality |
| Semantic Scholar API | Must validate real paper metadata and rate limit behavior |
| Crawl4AI / Chromium | Must validate real page extraction, BM25 filtering, JS rendering |
| Ollama / LLM | Must validate real inference quality and response parsing |
| ChromaDB | Must validate real embedding similarity and persistence |
| SQLite | Must validate real schema constraints and queries |
| `all-MiniLM-L6-v2` | Must validate real embedding quality for semantic retrieval |

## Pure Logic Tests (No Network Needed)

A small subset of tests don't inherently need internet:
- `test_chunker.py` — text splitting is pure logic
- `test_metadata_store.py` — SQLite operations are local
- Config parsing, logging setup

These still run as part of the full suite but are naturally fast and reliable.

---

## Quality Metrics

### Code Coverage Target
- **Unit tests**: >80% line coverage
- **Integration tests**: >60% branch coverage
- **E2E tests**: All happy paths covered

### Report Quality Metrics (E2E)
For evaluating generated reports:
1. **Completeness**: Does the report cover all sections from the research plan?
2. **Citation density**: Are >80% of factual claims cited?
3. **Source diversity**: Do citations come from >3 unique domains?
4. **Coherence**: Does the report read as a narrative (not a list)?
5. **Length**: Is the report >1500 words for substantive topics?
6. **Contradiction-free**: Are there no self-contradicting statements?

These are evaluated manually in v1, automated in v2.
