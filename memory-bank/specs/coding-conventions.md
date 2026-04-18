# Coding Conventions & Practices

Standards and patterns to follow throughout the LocalGPT Deep Research Agent codebase.

---

## Language & Runtime

- **Python 3.11+** (for `asyncio` improvements, `tomllib`, better type hints)
- **Async-first**: All I/O operations use `async/await` (crawling, search, LLM calls)
- **Type hints everywhere**: All function signatures, return types, class attributes

---

## Code Style

### Formatting
- **Formatter**: `black` (default config, line length 88)
- **Import sorting**: `isort` (compatible with black profile)
- **Linting**: `ruff` (replaces flake8, fast, comprehensive)

### Configuration (`pyproject.toml`)
```toml
[tool.black]
line-length = 88
target-version = ['py311']

[tool.isort]
profile = "black"

[tool.ruff]
line-length = 88
target-version = "py311"
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM", "RUF"]
```

### Naming Conventions
| Entity | Convention | Example |
|--------|-----------|---------|
| Files | snake_case | `ddg_search.py` |
| Classes | PascalCase | `VectorStore`, `TextChunker` |
| Functions | snake_case | `crawl_page`, `store_content` |
| Constants | UPPER_SNAKE | `CHUNK_SIZE`, `MAX_CONCURRENT` |
| Private members | _prefixed | `_parse_results`, `self._client` |
| Module-level loggers | `logger` | `logger = structlog.get_logger(__name__)` |

---

## Project Structure Conventions

### Package Layout
```
local_research_agent/
├── __init__.py          # Package version, public API
├── agent.py             # Root agent definition (entry point)
├── config.py            # All configuration in one place
├── logging_config.py    # Logging setup, called once at startup
├── sub_agents/          # One file per agent
├── tools/               # One file per tool
└── storage/             # Storage layer (self-contained module)
```

### Import Conventions
```python
# Standard library first
import asyncio
import hashlib
import logging
from pathlib import Path

# Third-party
import chromadb
import structlog
from google.adk.agents import LlmAgent
from sentence_transformers import SentenceTransformer

# Local
from local_research_agent.config import settings
from local_research_agent.storage import StorageManager
```

---

## Function Design Patterns

### Tool Functions
All ADK tool functions follow this pattern:

```python
import structlog
from google.adk.tools import FunctionTool

logger = structlog.get_logger(__name__)

def tool_function(param1: str, param2: int = 5) -> dict:
    """One-line summary for the LLM to understand.
    
    Detailed description of what this tool does, when to use it,
    and what it returns. The LLM reads this docstring.
    
    Args:
        param1: Clear description of param1.
        param2: Clear description with default noted.
    
    Returns:
        dict with 'status' ('success' or 'error') and 'data' or 'message'.
    """
    try:
        logger.info("tool_function.start", param1=param1, param2=param2)
        
        # ... implementation ...
        
        result = {"status": "success", "data": output}
        logger.info("tool_function.complete", result_size=len(output))
        return result
        
    except SpecificException as e:
        logger.warning("tool_function.expected_error", error=str(e))
        return {"status": "error", "message": f"Expected issue: {e}"}
        
    except Exception as e:
        logger.error("tool_function.unexpected_error", error=str(e), exc_info=True)
        return {"status": "error", "message": f"Unexpected error: {e}"}

# Export as ADK FunctionTool
tool_function_tool = FunctionTool(tool_function)
```

**Key rules:**
1. Tools **never raise exceptions** — they return error dicts
2. Tools **always log** entry and exit
3. Tools **return consistent structure** (`status` + `data/message`)
4. Docstrings are **LLM-readable** (the agent reads them to decide when to call the tool)
5. Type hints on all parameters (ADK uses these for tool schema generation)

### Async Tool Functions
Same pattern but with `async def`:
```python
async def async_tool(param: str) -> dict:
    """..."""
    try:
        result = await some_async_operation(param)
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

### Storage Layer Functions
```python
class StorageComponent:
    """Clear class docstring."""
    
    def __init__(self, config_param: str):
        self._client = create_client(config_param)
        self._logger = structlog.get_logger(self.__class__.__name__)
    
    def public_method(self, param: str) -> ReturnType:
        """What this does."""
        self._logger.info("method.start", param=param)
        result = self._internal_logic(param)
        self._logger.info("method.complete")
        return result
    
    def _internal_logic(self, param: str) -> ReturnType:
        """Private helper — no docstring needed for LLM."""
        ...
```

---

## Error Handling Philosophy

### Principle: Fail Gracefully, Log Everything

```
Layer 1: Tool functions → catch all exceptions, return error dicts
Layer 2: Agent prompts → instruct agents to handle tool errors
Layer 3: Orchestrator → handle agent failures, continue or abort
Layer 4: CLI runner → catch top-level errors, show user-friendly message
```

### Error Return Standard
```python
# Success
{"status": "success", "data": {...}}

# Expected error (network timeout, missing API key, etc.)
{"status": "error", "message": "Human-readable description"}

# Partial success (some items succeeded, some failed)
{"status": "partial", "data": [...], "failures": [...]}
```

### Logging Levels
| Level | Usage |
|-------|-------|
| `DEBUG` | Internal state, raw API responses (verbose) |
| `INFO` | Tool entry/exit, phase transitions, key metrics |
| `WARNING` | Expected failures (URL timeout, missing API key) |
| `ERROR` | Unexpected failures, exceptions with tracebacks |

---

## Configuration Management

### Single Source of Truth: `config.py`
```python
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    """All configuration with sensible defaults."""
    
    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"
    
    # Storage
    chroma_db_path: str = "./data/chroma_db"
    sqlite_db_path: str = "./data/research.db"
    reports_dir: str = "./reports"
    
    # Crawling
    crawl_max_concurrent: int = 3
    crawl_page_timeout: int = 30000
    crawl_headless: bool = True
    
    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    
    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 50
    
    # API Keys (optional)
    serp_api_key: str = ""
    semantic_scholar_api_key: str = ""
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
```

**Usage**: Always import `settings` — never read env vars directly in tool/agent code.
```python
from local_research_agent.config import settings

timeout = settings.crawl_page_timeout
```

---

## Logging Configuration

### Using `structlog`
```python
# logging_config.py
import structlog
import logging

def setup_logging(level: str = "INFO"):
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()  # Pretty console output
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level)
        ),
    )
```

### Log Output Example
```
2026-04-11T14:30:22Z [info     ] crawl_page.start           urls=['https://...'] query_context='mongol invasion'
2026-04-11T14:30:25Z [info     ] crawl_page.complete         results=3 failures=1
2026-04-11T14:30:25Z [warning  ] crawl_page.url_failed       url='https://...' error='Timeout'
```

---

## Documentation Standards

### Module Docstrings
Every `.py` file starts with a module docstring:
```python
"""
DuckDuckGo search tool for the web research agent.

Wraps the duckduckgo-search library as a Google ADK FunctionTool.
Returns structured search results with titles, URLs, and snippets.
"""
```

### Class Docstrings
```python
class VectorStore:
    """ChromaDB wrapper for storing and retrieving research content.
    
    Manages per-session collections with cosine similarity search.
    Uses persistent storage at the configured path.
    
    Usage:
        store = VectorStore("./data/chroma_db")
        store.add_documents("session_123", chunks, embeddings, metadatas)
        results = store.query("session_123", query_embedding, n_results=5)
    """
```

### Function Docstrings
```python
def store_content(session_id: str, url: str, content: str, title: str, source_type: str = "web") -> dict:
    """Store crawled web content in the hybrid storage layer.
    
    Chunks the content, generates embeddings, and stores in both
    ChromaDB (for semantic retrieval) and SQLite (for metadata tracking).
    
    Args:
        session_id: Active research session identifier.
        url: Source URL of the crawled content.
        content: Clean markdown content extracted by Crawl4AI.
        title: Page title.
        source_type: One of 'web', 'academic', 'scholar_abstract'.
    
    Returns:
        dict with:
            status: 'success' or 'error'
            chunks_stored: Number of chunks stored (if success)
            source_id: SQLite source ID (if success)
            message: Error message (if error)
    
    Raises:
        Never raises — all exceptions caught and returned as error dicts.
    """
```

---

## Git Conventions

### Branch Strategy
- `main` — stable, tested code
- `develop` — active development
- `feature/xxx` — feature branches off develop
- `fix/xxx` — bugfix branches

### Commit Message Format
```
type(scope): short description

Longer description if needed.

Types: feat, fix, docs, refactor, test, chore
Scopes: agent, tools, storage, config, tests
```

Examples:
```
feat(storage): implement ChromaDB vector store wrapper
fix(tools): handle DDG search timeout gracefully
docs(specs): add testing strategy document
test(storage): add chunker unit tests
```

### .gitignore
```
# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/

# Data (generated, not committed)
data/
reports/

# Environment
.env

# IDE
.idea/
.vscode/
*.swp

# Testing
.coverage
htmlcov/
.pytest_cache/
```

---

## Dependency Management

### requirements.txt (pinned for reproducibility)
```
# Pin major.minor, allow patch updates
google-adk>=0.3.0,<1.0
litellm>=1.0,<2.0
crawl4ai>=0.7.4,<1.0
chromadb>=0.5,<1.0
sentence-transformers>=3.0,<4.0
duckduckgo-search>=6.0,<7.0
google-search-results>=2.4,<3.0
httpx>=0.27,<1.0
python-dotenv>=1.0,<2.0
structlog>=24.0
pydantic-settings>=2.0,<3.0

# Dev dependencies
pytest>=8.0
pytest-asyncio>=0.23
pytest-mock>=3.0
pytest-cov>=5.0
black>=24.0
ruff>=0.5
isort>=5.0
```

### Adding New Dependencies
1. Add to `requirements.txt` with version bounds
2. Document in `techContext.md` with purpose
3. Validate no security vulnerabilities
4. Test that total installation doesn't break existing packages

---

## Performance Considerations

### Async Best Practices
```python
# DO: Use async for I/O operations
async def fetch_data():
    async with httpx.AsyncClient() as client:
        response = await client.get(url)

# DON'T: Block the event loop with sync I/O
def fetch_data():  # BAD — blocks async pipeline
    response = requests.get(url)

# DO: Use asyncio.gather for parallel I/O
results = await asyncio.gather(
    fetch_source_a(),
    fetch_source_b(),
    return_exceptions=True  # Don't fail-fast
)
```

### Memory Awareness
```python
# DO: Process chunks in batches
for batch in chunked(items, batch_size=32):
    embeddings = model.encode(batch)
    
# DON'T: Load everything into memory at once
all_embeddings = model.encode(all_10000_items)  # OOM risk

# DO: Close browser instances when done
async with AsyncWebCrawler(...) as crawler:
    results = await crawler.arun_many(urls)
# Browser is closed here — RAM freed

# DON'T: Keep browser instances alive between phases
```

### String Processing
```python
# DO: Use hashlib for content dedup
content_hash = hashlib.sha256(content.encode()).hexdigest()

# DO: Use generators for large text processing
def iter_paragraphs(text: str):
    for para in text.split("\n\n"):
        yield para.strip()
```

