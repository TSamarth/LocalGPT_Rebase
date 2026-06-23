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

**Goal**: one query → vetted markdown research report. **Google ADK 2.x + A2A protocol** orchestrates 6 specialized agents. All inference is local via Ollama.

> **Design version:** the committed target is **v2** ([memory-bank/architecture.md], ratified 2026-06-23): ADK 2.x dynamic workflows, **ADK-owned sessions/resume**, a single **local-first A2A boundary**, served via `adk api_server`. Code today (Stories 1–5) still implements the **v1 hand-rolled orchestrator**; v1→v2 migration is architecture.md §15. The contracts, agents, policy, and MCP layer are unchanged between v1 and v2 — only the orchestration shell + session ownership change.

**Data flow**: `Clarifier → Planner → [Acquirer → CP3? → Extractor → Verifier] loop → Writer`

**Agent contracts** — agents hand off typed Pydantic artifacts, never raw text:
- `ResearchPlan` (Planner → workflow)
- `ScoredURL[]` (Acquirer → Extractor) — includes `publication_date` + `citation_refs`
- `ClaimLedger` (Verifier → Writer) — contains `Claim` records with `temporal_status`, `confidence_score`, `conflict_type`
- In v2 a node is invoked via `await ctx.run_node(agent, input)`, which returns the output directly — no throwaway `Runner`/session per call.

**Orchestration (v2)**: an ADK **dynamic workflow** (`@node` / `Workflow` / `ctx.run_node`) is the control flow; deterministic Python (`stop_rule`, `depth_budget`, Verifier/Writer policy) decides control + policy, LLMs decide content. Replaces the v1 `orchestrator.py` + `stage_machine.py` driver + `pipeline.py` `_run_sync` bridge.

**Model strategy**: one hot Qwen2.5-14B role-prompted per agent + resident `nomic-embed-text:latest`. ~12.5 GB VRAM total. 16 GB RAM is the binding constraint — stages are strictly sequential to prevent OOM. This is also *why* the six stay **local sub-agents/nodes, not A2A peers** (A2A overhead buys no concurrency on one hot model). Set the **`OLLAMA_API_BASE` env var** at startup (LiteLLM routes non-generation calls through it).

**crawl4ai MCP** runs as a stdio subprocess attached via `ADK MCPToolset`, built **once per run and reused** across loop passes (then `close()`d) — not rebuilt per phase. Only Acquirer/Extractor/Verifier hold MCP tools (least-privilege). MCP stores content in SQLite (`crawled_pages`, `chunks`) + ChromaDB (cosine embeddings). Agents handoff page IDs, not text. **MCP stays the tool boundary; it is not migrated to A2A.**

**A2A boundary (v2, local-first)**: the whole pipeline is exposed as one `A2AServer` via `to_a2a(root_agent, port=8001)` or `adk api_server --a2a`, binding **localhost**. Consumed locally either by direct HTTP (`POST /run_sse`) or by another ADK agent via `RemoteA2aAgent(agent_card=…/.well-known/agent-card.json)`. Dep: `google-adk[a2a]`.

**Checkpoints** (human-in-loop, blocking — v2 via ADK `RequestInput`, resumable over api_server/SSE):
- CP1: after Planner, user approves/edits `ResearchPlan`
- CP2: after Writer, user approves/edits draft
- CP3: after Acquirer, before Extractor — only fires when `plan.depth == "deep"`

**Stages** (shared vocabulary + resume/observability anchors; in v2 they are positions in the workflow graph, not a hand-walked enum):
`INTAKE → CLARIFY → PLAN → [ACQUIRE → MID_ACQUIRE(CP3) → EXTRACT → VERIFY] → SYNTHESIZE → WRITE → DONE`

## Key Patterns

**Schemas are frozen contracts** (`orchestrator/app/schemas.py`). Agents are coded against them. Gate G1 requires all new optional fields added before Story 2 agents start (T0.4 — add `publication_date: date | None = None`, `citation_refs: list[str] = []` to `ScoredURL`; `temporal_status` to `Claim`; `confidence_score` + `conflict_type` to contradiction records). All additions must be optional with defaults to keep 14 existing tests green.

**Config** is a singleton dataclass (`config = Config()` at module bottom). New settings go there as `field(default_factory=lambda: _env(...))` — never hardcoded in agent logic.

**Session artifacts** live in `data/sessions/{id}/` as JSON files. **v1 (current):** `SessionStore` is the source of truth + resume engine (orchestrator reads `stage.json`, skips completed stages). **v2 (target):** ADK's session + event store owns state and resume (`App(resumability_config=ResumabilityConfig(is_resumable=True))`, resume by `invocation_id`); `data/sessions/*.json` is demoted to a **write-through export** (via an after-node callback/plugin) for inspection/diffing — `stage.json`-based resume is retired (architecture.md §5).

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
