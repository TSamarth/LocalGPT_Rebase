# Project Index: LocalGPT_Rebase (A2A Deep Research)

Generated: 2026-06-23

**Goal**: one query → vetted markdown research report. Google ADK + A2A protocol orchestrates 6 specialized agents. All inference local via Ollama. Web acquisition via crawl4ai MCP server.

> For working guidance read `CONTEXT.md`. For design depth read `memory-bank/`.

## 📁 Project Structure

```
LocalGPT_Rebase/
├── orchestrator/              # A2A pipeline package (own pyproject + .venv)
│   ├── app/
│   │   ├── agents/            # 6 agents: clarifier, planner, acquirer, extractor, verifier, writer
│   │   ├── schemas.py         # Pydantic v2 artifact contracts — frozen inter-agent API
│   │   ├── orchestrator.py    # stage transitions, agent wiring
│   │   ├── stage_machine.py   # Stage enum + transition rules
│   │   ├── checkpoint.py      # human-in-loop CP1/CP2/CP3 (blocking)
│   │   ├── session.py         # SessionStore: create/resume, JSON artifact persistence
│   │   ├── citation.py        # Semantic Scholar citation BFS (httpx, not an MCP tool)
│   │   ├── config.py          # singleton dataclass config from env/.env
│   │   └── llm.py             # LiteLlm backend → local Ollama
│   ├── scripts/check_setup.py # Gate G2 probe — verifies Ollama + models/VRAM
│   ├── main.py                # orchestrator entry point
│   └── tests/                 # 12 test files (pytest, asyncio_mode=auto)
├── mcp/Crawl4AI_MCP/          # FastMCP stdio server (own pyproject + .venv)
│   ├── app/
│   │   ├── server.py          # FastMCP entry; register_all() wires everything
│   │   ├── common.py          # single registration point for all tools
│   │   ├── tools/             # discover, triage, crawl, deep_crawl, adaptive_crawl, search, seed, dedup
│   │   ├── storage/           # sqlite_store + chroma_store + chunker (lazy singletons)
│   │   ├── resources/, prompts/, ratelimit.py, domain.py
│   ├── main.py                # run MCP server over stdio
│   ├── e2e_test.py            # live test (needs Ollama + network)
│   └── tests/                 # 7 test files (offline, crawl4ai mocked)
├── memory-bank/               # design docs (architecture, requirements, progress, *Context)
├── claudedocs/                # task hierarchy + workflow docs
├── CONTEXT.md                 # primary working guidance
└── CLAUDE.md / .claude/       # behavioral guidelines + memory bank
```

## 🚀 Entry Points

- **Orchestrator**: `orchestrator/main.py` — runs the A2A research pipeline
- **MCP server**: `mcp/Crawl4AI_MCP/main.py` — FastMCP over stdio (subprocess attached via ADK MCPToolset)
- **Setup gate**: `orchestrator/scripts/check_setup.py` — Gate G2, verifies Ollama + model VRAM
- **Tests**: `orchestrator/tests/`, `mcp/Crawl4AI_MCP/tests/`

## 🔄 Data Flow

```
Clarifier → Planner → [Acquirer → CP3? → Extractor → Verifier] loop → Writer
```

**Stage machine**:
`INTAKE → CLARIFY → PLAN → [ACQUIRE → MID_ACQUIRE(CP3) → EXTRACT → VERIFY] → SYNTHESIZE → WRITE → DONE`

**Checkpoints** (blocking, human-in-loop):
- CP1 — after Planner, approve/edit `ResearchPlan`
- CP2 — after Writer, approve/edit draft
- CP3 — after Acquirer, before Extractor; only fires when `plan.depth == "deep"`

## 📦 Core Modules

### orchestrator/app/agents/ — the 6 ADK agents
Hand off typed Pydantic artifacts, never raw text. Only Acquirer + Extractor hold MCP write tools.

### orchestrator/app/schemas.py — frozen contracts (Gate G1)
- `ResearchPlan` (Planner → Orchestrator)
- `ScoredURL[]` (Acquirer → Extractor) — `publication_date`, `citation_refs`
- `ClaimLedger` (Verifier → Writer) — `Claim` w/ `temporal_status`, `confidence_score`, `conflict_type`
- `StageState` — resume pointer (`data/sessions/{id}/stage.json`)
- New fields MUST be optional w/ defaults (keep existing tests green).

### mcp/Crawl4AI_MCP/app/tools/ — MCP tools
discover, triage, crawl, deep_crawl, adaptive_crawl, search, seed, dedup.
Register new tools in `app/common.py` via `mcp.tool()` — no scattered decorators.

### storage
SQLite (`crawled_pages`, `chunks`) + ChromaDB (cosine embeddings). Agents hand off page IDs, not text.

## 🔧 Configuration

- `orchestrator/pyproject.toml` — pkg `a2a-deep-research` v0.1.0, py≥3.11
- `mcp/Crawl4AI_MCP/pyproject.toml` — pkg `crawl4ai-mcp` v0.1.0, py≥3.11
- `config.py` (orchestrator) — singleton dataclass; new settings as `field(default_factory=lambda: _env(...))`
- ruff: line-length 120, select E/W/F/I/B; pytest asyncio_mode=auto

## 🔗 Key Dependencies

**orchestrator**: google-adk ≥0.3, litellm ≥1.0, ollama ≥0.6.1, pydantic ≥2.9, httpx, python-dotenv
**mcp**: crawl4ai ≥0.8.6, fastmcp ≥3.2.4, chromadb ≥1.5.8, aiosqlite, ddgs, tiktoken, tldextract, ollama, httpx

## 🧪 Tests

- orchestrator: 12 test files (test_schemas, _session, _stage_machine, _orchestrator, _checkpoint, _citation, + 6 agents)
- mcp: 7 test files (test_crawl, _deep_crawl, _discover, _search, _storage, _triage, _story2)
- Run: `cd <pkg> && uv run pytest tests/ -v`

## 🧠 Model Strategy

One hot Qwen2.5-14B role-prompted per agent + resident `nomic-embed-text:latest`. ~12.5 GB VRAM. 16 GB RAM is binding constraint → stages strictly sequential to prevent OOM.

## 📝 Quick Start

```bash
# orchestrator
cd orchestrator && uv sync
python scripts/check_setup.py        # Gate G2: verify Ollama + models
uv run pytest tests/ -v

# mcp server
cd mcp/Crawl4AI_MCP && uv sync
uv run crawl4ai-setup                # one-time: Playwright browsers
uv run python main.py                # run server over stdio
uv run pytest tests/ -v
```

## 📚 Design Docs

- `memory-bank/architecture.md` — agent roles, stage machine, citation BFS, contradiction scoring
- `memory-bank/requirements.md` — FR/NFR/AC (AC1–AC7)
- `memory-bank/progress.md` — built / next / decision log
- `claudedocs/spawn_a2a_research.md` — story-by-story tasks + gates
- `claudedocs/workflow_a2a_research.md` — task list, dependency graph, critical path
