# LocalGPT Deep Research Agent

A fully local, privacy-first deep research framework built with [Google ADK](https://google.github.io/adk-docs/) that autonomously researches any topic and generates comprehensive, fact-verified Markdown reports — all running on consumer hardware.

## Features

- **Multi-source research**: DuckDuckGo, Semantic Scholar, SerpAPI (optional)
- **Deep content extraction**: Crawl4AI with relevance-based content filtering
- **Fact verification**: Cross-references claims across sources, flags contradictions
- **Structured reports**: Well-organized Markdown with citations and source links
- **Hybrid storage**: ChromaDB (semantic search) + SQLite (metadata tracking)
- **Fully local**: No cloud LLM APIs — runs on Ollama with quantized 7B models
- **Privacy first**: Your queries and research never leave your machine

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU | NVIDIA GPU with 8GB VRAM | RTX 3070 or better |
| CPU | 4 cores / 8 threads | 6 cores / 12 threads |
| RAM | 12 GB | 16 GB |
| Storage | 2 GB free | 5 GB free |

## Quick Start

### Prerequisites
1. [Python 3.11+](https://python.org)
2. [Ollama](https://ollama.com) installed and running
3. NVIDIA GPU drivers + CUDA toolkit

### Installation
```bash
# Clone the repo
git clone <repo-url>
cd LocalGPT_Rebase

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Set up Crawl4AI browser
crawl4ai-setup

# Pull the LLM model
ollama pull mistral

# (Optional) Configure API keys
cp .env.example .env
# Edit .env to add SERP_API_KEY, etc.
```

### Usage
```bash
# Run a research query
python -m local_research_agent "Mongol Invasion of Japan in 1274"

# Output: reports/research_YYYYMMDD_HHMMSS.md
```

## Architecture

```
User Query
    │
    ▼
┌─────────────────┐
│  Query Planner   │  Decomposes query into sub-questions
└────────┬────────┘
         ▼
┌─────────────────────────────────┐
│  Research Phase (Parallel)       │
│  ┌─────────────┐ ┌─────────────┐│
│  │ Web Search  │ │  Academic   ││
│  │   Agent     │ │  Search     ││
│  │ DDG+Crawl4AI│ │ Scholar+SERP││
│  └─────────────┘ └─────────────┘│
└────────────────┬────────────────┘
                 ▼
┌─────────────────┐
│ Fact Verifier    │  Cross-references, flags contradictions
└────────┬────────┘
         ▼
┌─────────────────┐
│ Report Writer    │  Synthesizes verified facts → Markdown
└────────┬────────┘
         ▼
    📄 Report.md
```

## Project Structure

```
LocalGPT_Rebase/
├── local_research_agent/       # Main Python package
│   ├── agent.py                # Root agent orchestrator
│   ├── config.py               # Configuration management
│   ├── sub_agents/             # Specialized agents
│   ├── tools/                  # ADK tool wrappers
│   └── storage/                # ChromaDB + SQLite layer
├── tests/                      # Unit, integration, E2E tests
├── reports/                    # Generated research reports
├── memory-bank/                # Project documentation
├── requirements.txt
└── README.md
```

## Configuration

All configuration via environment variables (`.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `mistral` | Ollama model to use |
| `CRAWL_MAX_CONCURRENT` | `3` | Max concurrent browser instances |
| `CHUNK_SIZE` | `512` | Token size per text chunk |
| `SERP_API_KEY` | (empty) | Optional SerpAPI key |

See `.env.example` for all options.

## Documentation

Comprehensive documentation in `memory-bank/`:

| Document | Description |
|----------|-------------|
| [Project Brief](memory-bank/projectbrief.md) | Core requirements and goals |
| [Product Context](memory-bank/productContext.md) | Problem statement and UX goals |
| [System Patterns](memory-bank/systemPatterns.md) | Architecture and design patterns |
| [Tech Context](memory-bank/techContext.md) | Technology stack and constraints |
| [Agent Specs](memory-bank/specs/agent-specifications.md) | Detailed agent prompts and behavior |
| [Storage Design](memory-bank/specs/storage-layer-design.md) | Database schema and operations |
| [Crawl4AI Integration](memory-bank/specs/crawl4ai-integration.md) | Web crawling tool design |
| [Testing Strategy](memory-bank/specs/testing-strategy.md) | Testing approach and fixtures |
| [Coding Conventions](memory-bank/specs/coding-conventions.md) | Code style and patterns |
| [Limitations](memory-bank/specs/limitations.md) | Known constraints and trade-offs |
| [Architecture Decisions](memory-bank/architecture/decisions.md) | ADRs with rationale |

## License

[TBD]

