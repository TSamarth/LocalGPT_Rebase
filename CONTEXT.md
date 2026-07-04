# Context.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Structure

Two independent packages, each with its own `pyproject.toml` and `.venv`:

```
LocalGPT_Rebase/
├── orchestrator/          # A2A pipeline package (Stories 1–5 + v2 migration E0–E4 done)
│   ├── app/
│   │   ├── schemas.py          # Pydantic v2 artifact contracts — inter-agent API (frozen)
│   │   ├── config.py           # dataclass config loaded from env/.env
│   │   ├── llm.py              # one-hot model factory (build_model / build_agent)
│   │   │
│   │   ├── # — v2 live (ADK 2.x dynamic-workflow shell, v1 shell fully retired E5.S1) ———
│   │   ├── workflow.py         # @node research() — clarify→plan→CP1→loop→write→CP2
│   │   ├── adk_app.py          # App(name="localgpt_research", ResumabilityConfig)
│   │   ├── runner.py           # build_runner() factory; DatabaseSessionService (SQLite)
│   │   ├── session_exporter.py # SessionExporterPlugin — on_event/after_run → disk JSON
│   │   ├── research_policy.py  # pure stop_rule / merge_ledger / depth_budget
│   │   ├── cp3_adapter.py      # apply_cp3_verbs — free-text CP3 reply → (urls, needs_supplemental)
│   │   ├── server.py           # get_fast_api_app(a2a=False) + _mount_a2a (ADK 2.3.0 bug workaround)
│   │   ├── cli.py              # thin httpx REST CLI: POST /run_sse + inlined CP1/CP2 renderers + cp3_adapter
│   │   ├── a2a_client_example.py  # RemoteA2aAgent(use_legacy=False) consumption seam (doc/example)
│   │   ├── trace_plugin.py     # TracePlugin — per-node/model/tool spans to a per-session trace file (NEW 2026-07-04)
│   │   ├── maintenance.py      # opt-in session/export retention pruning, SESSION_RETENTION_DAYS (NEW 2026-07-04)
│   │   ├── session.py          # SessionStore: create/resume, JSON export (demoted; exporter owns writes)
│   │   ├── jsonio.py           # JSON retry + degrade for agent output
│   │   ├── citation.py         # Semantic Scholar BFS (httpx, not MCP)
│   │   └── agents/             # six specialist LlmAgents (Clarifier/Planner/Acquirer/Extractor/Verifier/Writer)
│   │       └── crawl4ai_toolset.py  # shared build_crawl4ai_toolset() + MCPSessionManager (per-invocation_id, resume-safe) (NEW 2026-07-04)
│   ├── localgpt_research/      # ADK discovery shim: re-exports the resumable App + agent.json card
│   ├── scripts/
│   │   ├── check_setup.py      # Gate G2 probe — verifies Ollama + models
│   │   ├── mem_probe.py        # RSS sampler for live acceptance runs (psutil)
│   │   └── acceptance_runbook.md  # user-run live-HW acceptance steps
│   └── tests/                  # 253 collected / 249 passing offline (pytest, asyncio_mode=auto); rest @pytest.mark.live
├── mcp/
│   └── Crawl4AI_MCP/           # FastMCP stdio server (fully implemented)
│       ├── app/
│       │   ├── server.py   # FastMCP entry; register_all() wires everything
│       │   ├── common.py   # Single registration point for all tools
│       │   ├── crawler.py  # persistent per-process AsyncWebCrawler (NEW 2026-07-04, replaces per-call launch/teardown)
│       │   ├── tools/      # discover, triage, crawl, deep_crawl, adaptive_crawl, search, seed, dedup
│       │   └── storage/    # sqlite_store.py + chroma_store.py (lazy singletons)
│       └── tests/          # 43 passing tests (offline suite, crawl4ai mocked)
├── memory-bank/           # Project design docs (architecture, requirements, progress)
└── claudedocs/            # Task hierarchy, workflow docs, and E-series execution plans
```

## Commands

### orchestrator package
```bash
cd orchestrator
uv sync                                  # install deps (pydantic, httpx, google-adk[a2a], etc.)
uv run pytest tests/ -v                  # run all tests (253 collected / 249 passing offline; rest @pytest.mark.live)
uv run pytest tests/test_schemas.py -v   # single test file
python scripts/check_setup.py           # verify Ollama + model VRAM (Gate G2)
uv run ruff check app/ tests/           # lint

# Run the pipeline server (REST /run_sse + A2A on localhost:8001)
uv run python -m app.server

# Drive via REST CLI (interactive HITL)
uv run python -m app.cli "your research query"
```

### mcp/Crawl4AI_MCP package
```bash
cd mcp/Crawl4AI_MCP
uv sync
uv run crawl4ai-setup                    # one-time: install Playwright browsers
uv run python main.py                    # run MCP server over stdio
uv run pytest tests/ -v                  # offline suite (43 green)
uv run python e2e_test.py               # live test (needs Ollama + network)
```

## Architecture

**Goal**: one query → vetted markdown research report. **Google ADK 2.x + A2A protocol** orchestrates 6 specialized agents. All inference is local via Ollama.

> **Design version: v2 — implemented, migration closed.** **ADK 2.x dynamic workflows**, **ADK-owned sessions/resume**, **`RequestInput` HITL**, single **local-first A2A boundary** served via `app/server.py` (`get_fast_api_app(a2a=False)` + explicit `_mount_a2a`). Migration steps E0–E5 all **committed to `dev`** (v1 shell deleted; 253 collected / 249 passing offline + 43 MCP green). v1→v2 migration path (historical): [architecture.md] §15. Current work is post-migration hardening — resource-optimization pass (shared crawl4ai MCP subprocess across HITL resume, persistent crawler, retention pruning, tracing fixes): [architecture.md] §16.

**Data flow**: `Clarifier → Planner →★CP1 → [Acquirer →★CP3? → Extractor → Verifier]* loop → Writer →★CP2`

**Agent contracts** — agents hand off typed Pydantic artifacts, never raw text:
- `ResearchPlan` (Planner → workflow)
- `ScoredURL[]` (Acquirer → Extractor) — includes `publication_date` + `citation_refs`
- `ClaimLedger` (Verifier → Writer) — contains `Claim` records with `temporal_status`, `confidence_score`, `conflict_type`
- In v2 a node is invoked via `await ctx.run_node(agent, input)`, which returns the output directly — no throwaway `Runner`/session per call.

**Orchestration (v2 — live)**: an ADK **dynamic workflow** (`@node` / `Workflow` / `ctx.run_node`) is the control flow; deterministic Python (`stop_rule`, `depth_budget`, Verifier/Writer policy) decides control + policy, LLMs decide content. The v2 shell in `workflow.py` / `adk_app.py` is the live driver; `orchestrator.py` + `stage_machine.py` + `pipeline.py` are the v1 remnants pending E5 retirement.

**Model strategy**: one hot Qwen2.5-14B role-prompted per agent + resident `nomic-embed-text:latest`. ~12.5 GB VRAM total. 16 GB RAM is the binding constraint — stages are strictly sequential to prevent OOM. This is also *why* the six stay **local sub-agents/nodes, not A2A peers** (A2A overhead buys no concurrency on one hot model). Set the **`OLLAMA_API_BASE` env var** at startup (LiteLLM routes non-generation calls through it).

**crawl4ai MCP** runs as a stdio subprocess attached via `ADK MCPToolset`, built through the shared `app/agents/crawl4ai_toolset.py` helper: an `MCPSessionManager` caches **one subprocess per `invocation_id`**, reused across loop passes AND HITL resume hops (released only on real error/completion, not on pause) — replaces the earlier "build once/run, close in finally" pattern. Only Acquirer/Extractor/Verifier hold MCP tools (least-privilege, differing only in `tool_filter`). MCP stores content in SQLite (`crawled_pages`, `chunks`) + ChromaDB (cosine embeddings), and holds a persistent per-process `AsyncWebCrawler` (`mcp/Crawl4AI_MCP/app/crawler.py`) instead of launching Chromium per call. Agents handoff page IDs, not text. **MCP stays the tool boundary; it is not migrated to A2A.**

**A2A boundary (v2 — live)**: the whole pipeline is exposed as one unified server via `get_fast_api_app(a2a=True)` in `app/server.py`, binding **localhost:8001**, serving both REST (`/run_sse`) and A2A (card + RPC). The agent card lives at `/a2a/localgpt_research/.well-known/agent-card.json`. ADK 2.3.0 has a `json`-shadow bug that 404s the auto-mounted card — `_mount_a2a` works around it (guarded no-op for future ADK). Consumed locally either by the thin `app/cli.py` or another ADK agent via `RemoteA2aAgent(agent_card=…/.well-known/agent-card.json, use_legacy=False)`. Dep: `google-adk[a2a]>=2.3,<3`.

**Session ownership (v2 — live)**: ADK's `DatabaseSessionService` (SQLite, `sqlite+aiosqlite`) owns session state + resume. `SessionExporterPlugin` (`on_event_callback` + `after_run_callback`) projects `v2_plan`/`v2_ledger`/`v2_draft` state-delta to `data/sessions/{id}/*.json` for inspection. `stage.json`-based resume is retired. Resume = pass the original `invocation_id` to the same `app.App`.

**Checkpoints** (human-in-loop, blocking — `RequestInput` nodes, resumable over api_server/SSE):
- CP1: after Planner, user approves/edits `ResearchPlan`
- CP2: after Writer, user approves/edits draft
- CP3: after Acquirer, before Extractor — only fires when `plan.depth == "deep"`
- Reject = re-yield `RequestInput` (NOT raise — raising is terminal/non-resumable in ADK 2.3.0)

**Stages** (shared vocabulary + resume/observability anchors; in v2 they are positions in the workflow graph, not a hand-walked enum):
`INTAKE → CLARIFY → PLAN → [ACQUIRE → MID_ACQUIRE(CP3) → EXTRACT → VERIFY] → SYNTHESIZE → WRITE → DONE`

## Key Patterns

**Schemas are frozen contracts** (`orchestrator/app/schemas.py`). Agents are coded against them. All optional fields have defaults so existing tests stay green.

**Config** is a singleton dataclass (`config = Config()` at module bottom). New settings go there as `field(default_factory=lambda: _env(...))` — never hardcoded in agent logic.

**Session artifacts** live in `data/sessions/{id}/` as JSON files — **written by `SessionExporterPlugin` as a write-through export** (not the resume source). ADK's `DatabaseSessionService` at `data/sessions.db` is the source of truth for resume. Final report writes to `data/reports/{id}.md`.

**research_policy.py is the shared policy source** — `stop_rule`, `depth_budget`, `merge_ledger` live here (pure, config+schemas-only imports). `stage_machine.py` re-exports them for the v1 shell; both share one implementation.

**Independence test** (Verifier): two sources are independent iff different `etld1` AND content cosine < `INDEPENDENCE_COSINE_THRESHOLD` (default 0.92). This threshold needs tuning against a labelled mirror set.

**Semantic Scholar citation BFS** (`orchestrator/app/citation.py`): httpx async utility, NOT an MCP tool. Called directly by Acquirer for `depth=deep` academic sources. 2-hop BFS, relevance-gated by LLM score ≥ 0.7.

## MCP Tool Registration

To add a new MCP tool: implement it in `mcp/Crawl4AI_MCP/app/tools/`, then register it in `app/common.py` with `mcp.tool()`. Do **not** use decorators scattered in tool files. See `mcp/Crawl4AI_MCP/CLAUDE.md` for full MCP-specific guidance.

## Design Docs

- `memory-bank/architecture.md` — agent roles, data flow, v2 ADK substrate, A2A topology, §15 migration status
- `memory-bank/requirements.md` — FR/NFR/AC (acceptance criteria AC1–AC7)
- `memory-bank/progress.md` — what's built, what's next, decision log
- `claudedocs/spawn_a2a_research.md` — story-by-story task breakdown with gates
- `claudedocs/workflow_a2a_research.md` — full task list with dependency graph and critical path
- `claudedocs/spawn_v1_to_v2_migration.md` — E-series execution log (E0–E4 done; E5 next)
- `claudedocs/workflow_v1_to_v2_migration.md` — P-series workflow with dependency graph (P0–P7)
- `claudedocs/E4_execution_plan.md` — A2A exposure execution plan (latest E-series)
