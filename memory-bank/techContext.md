# Tech Context

## Known / Fixed Technologies
- **Local inference**: Ollama (model selection TBD in design — constrained by RAM not just VRAM).
- **Source acquisition**: crawl4ai-backed MCP server (exists in this repo at `mcp/Crawl4AI_MCP/`).
  - Current capability: multi-source URL aggregation from SerpAPI + DuckDuckGo + arXiv.
  - To be extended (see requirements open questions).
- **Protocol**: MCP (Model Context Protocol) for tool exposure to agents.
- **Output**: Markdown files on disk (storage unconstrained).

## Hardware (hard constraint)
| Resource | Spec | Note |
|----------|------|------|
| GPU | RTX 4070 Ti Super | 16 GB VRAM |
| CPU | Ryzen 5 3600 | 6c/12t |
| RAM | 16 GB | **real bottleneck** |
| Storage | unconstrained | |
| OS | Windows 11 | shell: PowerShell + Git Bash |

## Implications
- 16 GB RAM forces sequential stage execution; cannot hold crawl4ai (browser/parsing) + large model + orchestrator hot at once.
- Prefer one shared role-prompted model OR small per-agent models loaded sequentially.
- Crawl concurrency must be bounded.

## Undecided (design phase)
- A2A framework: Google ADK vs. alternatives (LangGraph, CrewAI, custom, etc.) — **open to recommendation**.
- Specific Ollama model(s) and quantization.
- Orchestration runtime, state store, checkpoint UX surface.

## Repo Pointers
- Project root: `C:\Users\samar\PycharmProjects\GoogleADK\LocalGPT_Rebase`
- Existing MCP: `mcp/Crawl4AI_MCP/` (has its own memory-bank/).
- **A2A pipeline: `orchestrator/` (NEW, sibling to mcp/)** — `app/{schemas,config,session}.py`, `main.py`, `scripts/check_setup.py`, `tests/`.
- Skills available: `crawl4ai`, `fastmcp`, `mcp-builder`.

## Orchestrator Package (Story 1)
- Managed with `uv`. `pyproject.toml` deps: google-adk, ollama, pydantic>=2, python-dotenv, httpx.
- **Local venv is Python 3.13** (`orchestrator/.venv`, conda env `LocalGPT_Rebase`). Story 1 tests ran with light deps only (pydantic+dotenv+pytest) — google-adk NOT yet installed.
- Run tests: `.venv/Scripts/python -m pytest tests/ -q` (14 passing).
- Setup probe (G2): `python scripts/check_setup.py` — checks Ollama + models + `ollama ps` VRAM.
- Schemas use **Pydantic v2** (`.model_dump_json` / `.model_validate_json` for artifact persistence).