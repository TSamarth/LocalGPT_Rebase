# Tech Context

## Known / Fixed Technologies
- **Local inference**: Ollama (model pick TBD in design — RAM-bound not just VRAM).
- **Source acquisition**: crawl4ai-backed MCP server (lives in repo at `mcp/Crawl4AI_MCP/`).
  - Now: multi-source URL aggregation from SerpAPI + DuckDuckGo + arXiv.
  - Extend later (see requirements open questions).
- **Protocols**: **MCP** (Model Context Protocol) for tool exposure to agents; **A2A** (Agent2Agent) exposes whole pipeline as one local-first agent.
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
- 16 GB RAM forces sequential stage execution; can't hold crawl4ai (browser/parsing) + big model + orchestrator hot at once.
- Prefer one shared role-prompted model OR small per-agent models loaded sequentially.
- Crawl concurrency must stay bounded.

## Design Decisions (resolved — v2 ratified 2026-06-23)
- **Framework + major**: **Google ADK 2.x**, pinned `google-adk[a2a]>=2.3,<3` in `pyproject.toml` (✅ applied 2026-06-23 / E1.T1; was loose `>=0.3.0` spanning 1.x→2.0 break; lock=2.3.0, `[a2a]` extra pulls **a2a-sdk 0.3.26**). A2A protocol for pipeline boundary.
- **Models**: Qwen2.5-14B-Instruct Q4_K_M (reasoning, ~9 GB VRAM) + nomic-embed-text (embedding, ~0.5 GB). `keep_alive=-1`, 8B fallback. **`ollama_chat/` provider** + set **`OLLAMA_API_BASE` env var** at startup.
- **Orchestration runtime (v2)**: ADK **dynamic workflow** (`@node`/`Workflow`/`ctx.run_node`) under an `App`. Replaces v1 deterministic `app/stage_machine.py` + `app/orchestrator.py` + `app/pipeline.py` `_run_sync` driver.
- **Session/resume (v2)**: **ADK-owned** via `App(resumability_config=ResumabilityConfig(is_resumable=True))`, resume by `invocation_id`. `data/sessions/{id}/*.json` = write-through export only; `stage.json` retired.
- **Front-end / serving**: unified **`get_fast_api_app(a2a=True)`** (✅ E4/P5, `app/server.py`) — one localhost:8001 process serves REST (`/run_sse`, session CRUD, `/list-apps`) **and** A2A protocol (card + RPC); equiv CLI `adk api_server --a2a`. Thin httpx REST CLI = `app/cli.py`. Chosen over `to_a2a` (A2A-only, no `/run_sse`). Bound localhost.
- **Checkpoint UX (v2)**: ADK **`RequestInput`** nodes (resumable over api_server/SSE). CP1 (plan), CP2 (draft), CP3 (mid-acquisition, deep-only). Replaces v1 console `app/checkpoint.py`.
- **A2A boundary**: whole pipeline = one A2A server (local-first); 6 specialists stay local nodes on one hot model (not A2A peers) — A2A overhead buys no concurrency under RAM limit.
- *(v1, now superseded)*: deterministic stage machine wrapping LLM calls; `SessionStore` as truth + `stage.json` resume; CLI/`$EDITOR` checkpoints. Migration = architecture.md §15: steps 1–6 ✅ done (E0–E4 on `dev`); v1 shell deletion = E5 (still present till then for parity/imports).

## Repo Pointers
- Project root: `C:\Users\samar\PycharmProjects\GoogleADK\LocalGPTRebase` (dir renamed from `LocalGPT_Rebase` at some point — old refs elsewhere in this repo/memory bank are stale; conda env name `LocalGPT_Rebase` unaffected).
- Existing MCP: `mcp/Crawl4AI_MCP/` (has own memory-bank/). Now holds `app/crawler.py` — one long-lived `AsyncWebCrawler` per MCP server process (M2, 2026-07-04), replacing per-call launch/teardown of Chromium.
- **A2A pipeline: `orchestrator/`** (sibling to mcp/) — `app/{schemas,config,workflow,adk_app,runner,server,cli,...}.py`, `main.py`, `scripts/`, `tests/`.
- Skills available: `crawl4ai`, `fastmcp`, `mcp-builder`.

## Orchestrator Package (current state — v1→v2 migration + resource-optimization pass done, 2026-07-04)
- Managed with `uv`. `pyproject.toml` deps: **`google-adk[a2a]>=2.3,<3`** (lock=2.3.0, +a2a-sdk 0.3.26 transitive), ollama, pydantic>=2, python-dotenv, httpx, litellm>=1.0, **mcp** (required by ADK `MCPToolset`), `psutil` (mem_probe).
- **Local venv is Python 3.13.14** (`orchestrator/.venv`).
- Run tests: `.venv/Scripts/python.exe -m pytest -q` (from `orchestrator/`; `rtk cargo`-style `uv run pytest` also works when the `uv` trampoline resolves) — **253 orchestrator tests collected / 249 passing offline** (2 pre-existing failures gated behind `.env` `TARGET_EVIDENCE_SHALLOW=1`, 2 live-server tests excluded; 7 more are `@pytest.mark.live`-gated); +MCP 43 green.
- Setup probe (G2 — user must run): `python scripts/check_setup.py` — checks Ollama + models + `ollama ps` VRAM.
- Schemas use **Pydantic v2** (`.model_dump_json` / `.model_validate_json` for artifact persistence).
- **v1 shell fully deleted** (E5.S1, 2026-06-28) — no `orchestrator.py`/`pipeline.py`/`stage_machine.py`/`checkpoint.py` in the repo; do not reference them as live code.
- Key live modules: `app/{schemas,config,llm,session,jsonio,citation,research_policy,cp3_adapter,workflow,adk_app,runner,session_exporter,server,cli,a2a_client_example,trace_plugin,maintenance}.py`; `app/agents/{clarifier,planner,acquirer,extractor,verifier,writer,crawl4ai_toolset}.py`.
- **`app/agents/crawl4ai_toolset.py` (NEW, 2026-07-04, resource-optimization H1/H2/M1)** — single `build_crawl4ai_toolset(tool_filter)` helper shared by Acquirer/Extractor/Verifier (kills 3x triplicated `_build_default_toolset` copies). An `MCPSessionManager` caches one crawl4ai stdio subprocess **per `invocation_id`**, reused across HITL resume hops (released only on real error/completion, not on `NodeInterruptedError` pause) instead of rebuilding per node call. Connection timeout now `config.MCP_TOOL_TIMEOUT_SEC` (default 180s, was hardcoded up to 500s worst-case).
- **`app/trace_plugin.py` (NEW)** — `TracePlugin` (ADK plugin) emits per-node/model/tool spans to a per-session trace file; holds one open file handle per session instead of open/write/close+mkdir per callback (M6), and pops its `_starts` dict entries on error callbacks too, not just success (M5 leak fix).
- **`app/maintenance.py` (NEW)** — opt-in retention pruning for `data/sessions.db` + per-session export dirs, gated by `config.SESSION_RETENTION_DAYS` (default `0` = disabled). Not wired into any startup path by design — must be invoked explicitly (cron/manual).
- **Config additions (2026-07-04)**: `MCP_TOOL_TIMEOUT_SEC` (float, default 180.0), `SESSION_RETENTION_DAYS` (int, default 0).
- **ADK 2.x dynamic-workflow API anchors (verified against installed 2.3.0):** `from google.adk import Context, Workflow`; `from google.adk.workflow import node, START`; **`from google.adk.events import RequestInput`** (NOT `google.adk.workflow`); `from google.adk.apps import App, ResumabilityConfig`; `from google.adk.runners import InMemoryRunner`. Gotchas: START-entry node's passthrough param must be named **`node_input`**; `ctx.run_node(...)` returns node's `BaseModel` output as **`model_dump()` dict** (coerce via agents' `parse_*` helpers); resume via `runner.run_async(..., invocation_id=...)`; reject = re-yield `RequestInput`, never raise (raise = terminal, non-resumable).