# Project Index: LocalGPT_Rebase (A2A Deep Research)

Generated: 2026-06-29

**Goal**: one query → vetted markdown research report. Google ADK + A2A protocol orchestrates 6 specialized agents. All inference local via Ollama. Web acquisition via crawl4ai MCP server.

> For working guidance read `CONTEXT.md`. For design depth read `memory-bank/`.

## 📁 Project Structure

```
LocalGPT_Rebase/
├── orchestrator/              # A2A pipeline package (own pyproject + .venv)
│   ├── app/
│   │   ├── agents/            # 6 agents: clarifier, planner, acquirer, extractor, verifier, writer
│   │   ├── schemas.py         # Pydantic v2 artifact contracts — frozen inter-agent API
│   │   ├── workflow.py        # v2 ADK dynamic Workflow (clarify→plan→research loop→write)
│   │   ├── adk_app.py         # ADK App (resumable) + SessionExporterPlugin wired
│   │   ├── runner.py          # build_runner() factory — merges plugins into App copy
│   │   ├── server.py          # FastAPI REST (/run_sse) + A2A protocol; localhost only
│   │   ├── cli.py             # thin httpx REST CLI over /run_sse; CP1/CP2/CP3 renderers
│   │   ├── research_policy.py # pure policy: depth_budget, merge_ledger, stop_rule
│   │   ├── cp3_adapter.py     # apply_cp3_verbs (+add/-exclude/r/d grammar); pure
│   │   ├── session.py         # SessionStore: create/resume, JSON artifact persistence
│   │   ├── session_exporter.py# SessionExporterPlugin: on_event + after_run → disk
│   │   ├── citation.py        # Semantic Scholar citation BFS (httpx, not MCP)
│   │   ├── jsonio.py          # invoke_json_with_retry + degrade
│   │   ├── config.py          # singleton dataclass config from env/.env
│   │   └── llm.py             # LiteLlm backend → local Ollama
│   ├── localgpt_research/     # re-export package for `adk` CLI discovery (agent.json)
│   ├── scripts/check_setup.py # Gate G2 probe — verifies Ollama + models/VRAM
│   ├── main.py                # in-process entry: drives v2 workflow, answers CP pauses
│   └── tests/                 # 23 test files (pytest, asyncio_mode=auto)
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
├── claudedocs/                # task hierarchy + workflow docs + execution plans
├── CONTEXT.md                 # primary working guidance
└── CLAUDE.md / .claude/       # behavioral guidelines + memory bank
```

## 🚀 Entry Points

- **In-process run**: `orchestrator/main.py` — drives v2 ADK workflow in-process; answers CP1/CP2/CP3 `RequestInput` pauses from console via `cli._answer_checkpoint`
- **REST + A2A server**: `orchestrator/app/server.py` — FastAPI on localhost; `/run_sse` + A2A protocol (`/a2a/localgpt_research`)
- **REST CLI**: `orchestrator/app/cli.py` — `run_cli()` drives a run over HTTP against `app/server.py`
- **MCP server**: `mcp/Crawl4AI_MCP/main.py` — FastMCP over stdio (subprocess attached via ADK MCPToolset)
- **Setup gate**: `orchestrator/scripts/check_setup.py` — Gate G2, verifies Ollama + model VRAM
- **Tests**: `orchestrator/tests/` (23 files), `mcp/Crawl4AI_MCP/tests/` (7 files)

## 🔄 Data Flow

```
Clarifier → Planner → CP1 → [Acquirer → CP3? → Extractor → Verifier] loop → Writer → CP2
```

**v2 ADK dynamic Workflow** (`workflow.py`):
- `research` parent node (`@node(rerun_on_resume=True)`) runs clarify→plan→CP1→research loop→write
- Sub-nodes called via `ctx.run_node(LlmAgent)` — no per-call Runner, no manual session plumbing
- HITL pauses via ADK `RequestInput` yield (CP1 plan approval, CP2 draft approval, CP3 deep-only source inspect)
- Resume by `invocation_id` — ADK auto-checkpoints each `run_node`

**Checkpoints** (blocking, human-in-loop via `RequestInput`):
- CP1 — after Planner, approve/edit `ResearchPlan`
- CP2 — after Writer, approve/edit draft
- CP3 — after Acquirer, before Extractor; only fires when `plan.depth == "deep"`

**Session persistence**: ADK `DatabaseSessionService` (SQLite, `sqlite+aiosqlite`); `SessionExporterPlugin` writes `v2_plan`/`v2_ledger`/`v2_draft` to `data/sessions/{id}/` on events.

## 📦 Core Modules

### orchestrator/app/workflow.py — v2 ADK dynamic Workflow
`build_research_workflow(clarifier_node, planner_node, ...)` → `Workflow`. DI seam via closure so tests inject offline `@node` stubs. Research loop driven by `research_policy.py` (`depth_budget`, `stop_rule`, `merge_ledger`).

### orchestrator/app/adk_app.py — the App
```python
app = App(name="localgpt_research", root_agent=build_research_workflow(),
          resumability_config=ResumabilityConfig(is_resumable=True),
          plugins=[SessionExporterPlugin()])
```
Module-level `app` symbol — ADK CLI convention. Import offline-safe (LiteLlm lazy).

### orchestrator/app/schemas.py — frozen contracts (Gate G1)
- `ResearchPlan` (Planner → CP1 → Orchestrator)
- `ScoredURL[]` (Acquirer → CP3 → Extractor) — `publication_date`, `citation_refs`
- `ClaimLedger` (Verifier → Writer → CP2) — `Claim` w/ `temporal_status`, `confidence_score`, `conflict_type`
- New fields MUST be optional w/ defaults (keep existing tests green).

### orchestrator/app/research_policy.py — pure policy (no ADK)
`depth_budget(plan)` → iteration count. `stop_rule(ledger, budget)` → bool. `merge_ledger(base, patch)` → merged ledger. Shared between v2 workflow and tests; import-independent.

### orchestrator/app/cp3_adapter.py — CP3 verb grammar
`apply_cp3_verbs(urls, verbs)` → filtered URLs. Pure (no I/O). `+add`/`-exclude`/`r redirect`/`d done` grammar.

### orchestrator/app/server.py — REST + A2A server
`get_fast_api_app(a2a=True)` + hand-rolled `_mount_a2a` (workaround for ADK 2.3.0 `json`-shadow bug). Localhost only. Shares `DatabaseSessionService` between REST and A2A surfaces.

### orchestrator/app/cli.py — REST CLI + checkpoint renderers
`run_cli()` drives a full run over HTTP. `_render_cp1`/`_render_cp2` inlined here (approve/edit/reject). CP3 verbs via `cp3_adapter.apply_cp3_verbs`. Also used by `main.py` in-process path.

### orchestrator/app/agents/ — the 6 ADK LlmAgents
`build_clarifier`, `build_planner`, `build_acquirer`, `build_extractor`, `build_verifier`, `build_writer`. Called via `ctx.run_node()` in workflow. Only Acquirer + Extractor hold MCP toolsets (opened once per workflow pass, closed in `finally`).

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

**orchestrator**: `google-adk[a2a]>=2.3,<3` (lock 2.3.0 + `a2a-sdk` 0.3.26), litellm ≥1.0, ollama ≥0.6.1, pydantic ≥2.9, httpx, python-dotenv, aiosqlite (DatabaseSessionService), fastapi
**mcp**: crawl4ai ≥0.8.6, fastmcp ≥3.2.4, chromadb ≥1.5.8, aiosqlite, ddgs, tiktoken, tldextract, ollama, httpx

## 🧪 Tests

- orchestrator: **23 test files** — test_schemas, _session, _workflow, _research_policy, _cp3_adapter, _citation, _jsonio, _llm, _session_exporter (+ E3/E4 integration: _a2a_hitl_gate, _server, _cli, _a2a_client_example, _a2a_roundtrip, _toolset_lifecycle + 6 agent tests)
- mcp: 7 test files (test_crawl, _deep_crawl, _discover, _search, _storage, _triage, _story2)
- **231 orchestrator** (230 green offline + 1 needs live Ollama) + **43 MCP** green
- Run: `cd <pkg> && uv run pytest tests/ -v`

## 🧠 Model Strategy

One hot Qwen2.5-14B role-prompted per agent + resident `nomic-embed-text:latest`. ~12.5 GB VRAM. 16 GB RAM is binding constraint → stages strictly sequential to prevent OOM. `OLLAMA_API_BASE` configurable via env.

## 📝 Quick Start

```bash
# orchestrator (in-process)
cd orchestrator && uv sync
python scripts/check_setup.py        # Gate G2: verify Ollama + models
uv run python main.py "your research query"

# orchestrator (REST + A2A server)
uv run python -m app.server          # starts localhost server
uv run python -m app.cli "query"     # drives run over HTTP

# mcp server
cd mcp/Crawl4AI_MCP && uv sync
uv run crawl4ai-setup                # one-time: Playwright browsers
uv run python main.py                # run server over stdio
uv run pytest tests/ -v
```

## 📚 Design Docs

- `memory-bank/architecture.md` — agent roles, v2 workflow, citation BFS, contradiction scoring
- `memory-bank/requirements.md` — FR/NFR/AC (AC1–AC7)
- `memory-bank/progress.md` — built / next / decision log
- `claudedocs/spawn_a2a_research.md` — story-by-story tasks + gates
- `claudedocs/spawn_v1_to_v2_migration.md` — E1–E5 migration execution log
- `claudedocs/workflow_a2a_research.md` — task list, dependency graph, critical path

## 🗑️ Deleted (v1 shell — E5.S1)

`orchestrator.py`, `pipeline.py`, `stage_machine.py`, `checkpoint.py` — all deleted. v1 test files deleted: `test_orchestrator.py`, `test_pipeline.py`, `test_checkpoint.py`, `test_stage_machine.py`, `test_parity_v1_v2.py`. Policy tests migrated → `test_research_policy.py`.
