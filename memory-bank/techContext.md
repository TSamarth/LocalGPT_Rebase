# Tech Context: LocalGPT Deep Research Agent

## Technology Stack

### Core Framework
| Technology | Version | Purpose |
|-----------|---------|---------|
| **Python** | 3.11+ | Primary language |
| **Google ADK** | >=0.3.0 | Agent orchestration framework |
| **LiteLLM** | latest | LLM abstraction — connects ADK to Ollama |

### LLM Infrastructure
| Technology | Version | Purpose |
|-----------|---------|---------|
| **Ollama** | latest | Local LLM server with OpenAI-compatible API |
| **Mistral 7B** (Q4_K_M) | v0.3 | Primary LLM. Alt: Qwen2.5 7B Instruct Q4_K_M |
| **all-MiniLM-L6-v2** | v2 | Embedding model (CPU-only, sentence-transformers) |

### Web Crawling & Search
| Technology | Version | Purpose |
|-----------|---------|---------|
| **Crawl4AI** | >=0.7.4 | Async web crawler with Markdown extraction |
| **duckduckgo-search** | latest | DDG search wrapper (no key needed) |
| **google-search-results** | latest | SerpAPI client (optional key) |
| **httpx** | latest | Async HTTP for Semantic Scholar API |

### Storage
| Technology | Version | Purpose |
|-----------|---------|---------|
| **ChromaDB** | latest | Embedded vector DB for semantic search |
| **SQLite** | stdlib | Metadata: sources, sessions, query plans |
| **sentence-transformers** | latest | Embedding generation for ChromaDB |

### Development & Testing
| Technology | Version | Purpose |
|-----------|---------|---------|
| **pytest** | latest | Test framework |
| **pytest-asyncio** | latest | Async test support |
| **pytest-retry** | latest | Auto-retry for flaky API-dependent tests |
| **pytest-timeout** | latest | Prevent hung tests |
| **python-dotenv** | latest | Env var management |
| **structlog** | latest | Structured logging |

## Development Setup

### Prerequisites
1. **Python 3.11+** on PATH
2. **Ollama** installed and running
3. **Git** for version control
4. **NVIDIA drivers + CUDA** for GPU acceleration

### Installation Steps
```bash
# 1. Clone and enter project
git clone <repo-url> LocalGPT_Rebase && cd LocalGPT_Rebase

# 2. Create & activate virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set up Crawl4AI headless browser
crawl4ai-setup

# 5. Pull the LLM model
ollama pull mistral
# OR: ollama pull qwen2.5:7b-instruct-q4_K_M

# 6. Verify Ollama is serving
curl http://localhost:11434/api/tags

# 7. Configure environment (optional API keys)
cp .env.example .env

# 8. Run health check
python -m local_research_agent.healthcheck
```

### Environment Variables (.env)
```bash
# Optional: enhanced search
SERP_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=

# Ollama (defaults)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral

# Storage (defaults)
CHROMA_DB_PATH=./data/chroma_db
SQLITE_DB_PATH=./data/research.db
REPORTS_DIR=./reports

# Crawl4AI (defaults)
CRAWL_MAX_CONCURRENT=3
CRAWL_PAGE_TIMEOUT=30000
CRAWL_HEADLESS=true

# Embeddings (defaults)
EMBEDDING_MODEL=all-MiniLM-L6-v2
EMBEDDING_DEVICE=cpu

# Chunking (defaults)
CHUNK_SIZE=512
CHUNK_OVERLAP=50
```

### requirements.txt
```
google-adk>=0.3.0
litellm
crawl4ai>=0.7.4
duckduckgo-search
google-search-results
httpx
chromadb
sentence-transformers
python-dotenv
structlog
pytest
pytest-asyncio
pytest-retry
pytest-timeout
```

## Technical Constraints

### Hardware Budget
| Resource | Total | OS Reserve | App Budget | Allocation |
|----------|-------|-----------|------------|------------|
| GPU VRAM | 8 GB | 0.5 GB | ~7.5 GB | Ollama LLM ~5.5 GB + KV cache ~1.5 GB |
| RAM | 16 GB | ~3 GB | ~13 GB | Ollama ~1 GB + Chromium ~1 GB + ChromaDB ~0.5 GB + rest |
| CPU | 6c/12t | OS | ~10 threads | Embeddings + async I/O + crawling |

### Memory Budget (Peak)
```
Component                     RAM (MB)    VRAM (MB)
Ollama server process           800         -
Mistral 7B Q4_K_M                -       5,500
KV cache (4K context)             -       1,500
Crawl4AI (3 × 300MB)            900         -
ChromaDB embedded               400         -
Embedding model MiniLM           100         -
Python runtime + ADK             300         -
SQLite                            50         -
TOTAL                          2,550       7,000
Available                     13,000       7,500
Headroom                      10,450         500
```

### Performance Expectations
| Phase | Duration | Bottleneck |
|-------|----------|------------|
| Query Planning | 5–15 sec | LLM inference |
| Search (DDG + Scholar) | 5–15 sec | Network I/O |
| Web Crawling (10-15 URLs) | 30–90 sec | Network + browser |
| Content Storage | 5–10 sec | CPU embedding |
| Fact Verification | 30–60 sec | LLM inference |
| Report Writing | 30–90 sec | LLM inference |
| **Total** | **~2–5 min** | Mixed |

### Context Window Limits
- Mistral 7B: **8,192 tokens** (expandable to 32K, quality degrades)
- Qwen2.5 7B: **32,768 tokens** native (better for long context)
- Mitigation: chunked retrieval, summaries in state, section-by-section generation

## External APIs
| Service | Required? | Rate Limits | Auth |
|---------|-----------|-------------|------|
| DuckDuckGo | Yes | Informal | None |
| Semantic Scholar | Yes | 100 req/5min | Optional key |
| SerpAPI | No | Plan-based | API key |
| Ollama (local) | Yes | Unlimited | None |

## Key Code Patterns

### Google ADK Agent Definition
```python
from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
from google.adk.models.lite_llm import LiteLlm

model = LiteLlm(model="ollama/mistral")
agent = LlmAgent(
    name="agent_name",
    model=model,
    instruction="System prompt...",
    tools=[tool1, tool2],
    output_key="session_state_key"
)
```

### Crawl4AI Research Pattern
```python
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.content_filter_strategy import BM25ContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

bm25 = BM25ContentFilter(user_query=query, bm25_threshold=1.0)
md_gen = DefaultMarkdownGenerator(content_filter=bm25)
config = CrawlerRunConfig(
    markdown_generator=md_gen,
    excluded_tags=["nav", "footer", "aside"],
    remove_overlay_elements=True,
    page_timeout=30000
)
async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
    results = await crawler.arun_many(urls, config=config, max_concurrent=3)
```

### ChromaDB Embedded Pattern
```python
import chromadb
client = chromadb.PersistentClient(path="./data/chroma_db")
collection = client.get_or_create_collection(
    name=f"session_{session_id}",
    metadata={"hnsw:space": "cosine"}
)
collection.add(documents=chunks, embeddings=embeddings,
    metadatas=[{"source_url": url}], ids=[f"{hash}_{i}" for i in range(len(chunks))])
results = collection.query(query_embeddings=[qe], n_results=5)
```

## Directory Structure
```
LocalGPT_Rebase/
├── local_research_agent/
│   ├── __init__.py
│   ├── agent.py                # Root SequentialAgent
│   ├── config.py               # Configuration management
│   ├── logging_config.py       # Structured logging setup
│   ├── healthcheck.py          # Pre-flight checks
│   ├── sub_agents/
│   │   ├── __init__.py
│   │   ├── query_planner.py
│   │   ├── web_search.py
│   │   ├── academic_search.py
│   │   ├── fact_verifier.py
│   │   └── report_writer.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── ddg_search.py
│   │   ├── serp_search.py
│   │   ├── scholar_search.py
│   │   ├── crawl_tool.py
│   │   ├── store_tool.py
│   │   ├── retrieve_tool.py
│   │   └── file_writer.py
│   └── storage/
│       ├── __init__.py
│       ├── vector_store.py
│       ├── metadata_store.py
│       └── chunker.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── data/
│   ├── chroma_db/
│   └── research.db
├── reports/
├── memory-bank/
├── .env / .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml
└── README.md
```
placeholder
