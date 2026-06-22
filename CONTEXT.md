# Context.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Structure

Two independent packages, each with its own `pyproject.toml` and `.venv`:

```
LocalGPT_Rebase/
├── orchestrator/          # A2A pipeline package (Story 1 done; agents TBD)
│   ├── app/
│   │   ├── schemas.py     # Pydantic v2 artifact contracts — the inter-agent API
│   │   ├── config.py      # dataclass config loaded from env/.env
│   │   └── session.py     # SessionStore: create/resume, JSON artifact persistence
│   ├── scripts/
│   │   └── check_setup.py # Gate G2 probe — verifies Ollama + models
│   └── tests/             # 14 passing tests (pytest, asyncio_mode=auto)
├── mcp/
│   └── Crawl4AI_MCP/      # FastMCP stdio server (fully implemented)
│       ├── app/
│       │   ├── server.py  # FastMCP entry; register_all() wires everything
│       │   ├── common.py  # Single registration point for all tools
│       │   ├── tools/     # discover, triage, crawl, deep_crawl, adaptive_crawl, search
│       │   └── storage/   # sqlite_store.py + chroma_store.py (lazy singletons)
│       └── tests/         # offline suite (crawl4ai mocked)
├── memory-bank/           # Project design docs (architecture, requirements, progress)
└── claudedocs/            # Task hierarchy and workflow docs
```

## Commands

### orchestrator package
```bash
cd orchestrator
uv sync                                  # install deps (pydantic, httpx, google-adk, etc.)
uv run pytest tests/ -v                  # run all tests
uv run pytest tests/test_schemas.py -v   # single test file
python scripts/check_setup.py           # verify Ollama + model VRAM (Gate G2)
uv run ruff check app/ tests/           # lint
```

### mcp/Crawl4AI_MCP package
```bash
cd mcp/Crawl4AI_MCP
uv sync
uv run crawl4ai-setup                    # one-time: install Playwright browsers
uv run python main.py                    # run MCP server over stdio
uv run pytest tests/ -v                  # offline suite
uv run python e2e_test.py               # live test (needs Ollama + network)
```

## Architecture

**Goal**: one query → vetted markdown research report. Google ADK + A2A protocol orchestrates 6 specialized agents. All inference is local via Ollama.

**Data flow**: `Clarifier → Planner → [Acquirer → CP3? → Extractor → Verifier] loop → Writer`

**Agent contracts** — agents hand off typed Pydantic artifacts, never raw text:
- `ResearchPlan` (Planner → Orchestrator)
- `ScoredURL[]` (Acquirer → Extractor) — will include `publication_date` + `citation_refs` after T0.4
- `ClaimLedger` (Verifier → Writer) — contains `Claim` records with `temporal_status`, `confidence_score`, `conflict_type`
- `StageState` — lightweight pointer to resume interrupted runs (`data/sessions/{id}/stage.json`)

**Model strategy**: one hot Qwen2.5-14B-Instruct Q4_K_M role-prompted per agent + resident `nomic-embed-text`. ~12.5 GB VRAM total. 16 GB RAM is the binding constraint — stages are strictly sequential to prevent OOM.

**crawl4ai MCP** runs as a stdio subprocess attached via `ADK MCPToolset`. Only Acquirer and Extractor hold MCP write tools. MCP stores content in SQLite (`crawled_pages`, `chunks`) + ChromaDB (cosine embeddings). Agents handoff page IDs, not text.

**Checkpoints** (human-in-loop, blocking):
- CP1: after Planner, user approves/edits `ResearchPlan`
- CP2: after Writer, user approves/edits draft
- CP3: after Acquirer, before Extractor — only fires when `plan.depth == "deep"`

**Stage machine** (in `Stage` enum, orchestrator manages transitions):
`INTAKE → CLARIFY → PLAN → [ACQUIRE → MID_ACQUIRE(CP3) → EXTRACT → VERIFY] → SYNTHESIZE → WRITE → DONE`

## Key Patterns

**Schemas are frozen contracts** (`orchestrator/app/schemas.py`). Agents are coded against them. Gate G1 requires all new optional fields added before Story 2 agents start (T0.4 — add `publication_date: date | None = None`, `citation_refs: list[str] = []` to `ScoredURL`; `temporal_status` to `Claim`; `confidence_score` + `conflict_type` to contradiction records). All additions must be optional with defaults to keep 14 existing tests green.

**Config** is a singleton dataclass (`config = Config()` at module bottom). New settings go there as `field(default_factory=lambda: _env(...))` — never hardcoded in agent logic.

**Session artifacts** live in `data/sessions/{id}/` as JSON files. `SessionStore` is pure I/O — no logic. On resume, the orchestrator reads `stage.json` and skips completed stages.

**Independence test** (Verifier): two sources are independent iff different `etld1` AND content cosine < `INDEPENDENCE_COSINE_THRESHOLD` (default 0.92). This threshold needs tuning against a labelled mirror set.

**Semantic Scholar citation BFS** (T1.6, planned): `orchestrator/app/citation.py`, httpx async utility. NOT an MCP tool. Called directly by Acquirer for `depth=deep` academic sources. 2-hop BFS, relevance-gated by LLM score ≥ 0.7.

## MCP Tool Registration

To add a new MCP tool: implement it in `mcp/Crawl4AI_MCP/app/tools/`, then register it in `app/common.py` with `mcp.tool()`. Do **not** use decorators scattered in tool files. See `mcp/Crawl4AI_MCP/CLAUDE.md` for full MCP-specific guidance.

## Design Docs

- `memory-bank/architecture.md` — agent roles, stage machine, data flow, citation BFS design, contradiction confidence scoring
- `memory-bank/requirements.md` — FR/NFR/AC (acceptance criteria AC1–AC7)
- `memory-bank/progress.md` — what's built, what's next, decision log
- `claudedocs/spawn_a2a_research.md` — story-by-story task breakdown with gates
- `claudedocs/workflow_a2a_research.md` — full task list with dependency graph and critical path
