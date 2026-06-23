# Tech Context

## Known / Fixed Technologies
- **Local inference**: Ollama (model selection TBD in design — constrained by RAM not just VRAM).
- **Source acquisition**: crawl4ai-backed MCP server (exists in this repo at `mcp/Crawl4AI_MCP/`).
  - Current capability: multi-source URL aggregation from SerpAPI + DuckDuckGo + arXiv.
  - To be extended (see requirements open questions).
- **Protocols**: **MCP** (Model Context Protocol) for tool exposure to agents; **A2A** (Agent2Agent) for exposing the whole pipeline as one local-first agent.
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

## Design Decisions (resolved — v2 ratified 2026-06-23)
- **Framework + major**: **Google ADK 2.x**, pinned `google-adk[a2a]>=2.3,<3` in `pyproject.toml` (✅ applied 2026-06-23 / E1.T1; was loose `>=0.3.0` spanning the 1.x→2.0 break; lock=2.3.0, `[a2a]` extra pulls **a2a-sdk 0.3.26**). A2A protocol for the pipeline boundary.
- **Models**: Qwen2.5-14B-Instruct Q4_K_M (reasoning, ~9 GB VRAM) + nomic-embed-text (embedding, ~0.5 GB). `keep_alive=-1`, 8B fallback. **`ollama_chat/` provider** + set **`OLLAMA_API_BASE` env var** at startup.
- **Orchestration runtime (v2)**: ADK **dynamic workflow** (`@node`/`Workflow`/`ctx.run_node`) under an `App`. Replaces the v1 deterministic `app/stage_machine.py` + `app/orchestrator.py` + `app/pipeline.py` `_run_sync` driver.
- **Session/resume (v2)**: **ADK-owned** via `App(resumability_config=ResumabilityConfig(is_resumable=True))`, resume by `invocation_id`. `data/sessions/{id}/*.json` = write-through export only; `stage.json` retired.
- **Front-end / serving**: **`adk api_server`** (local service) + thin CLI. A2A exposure via `to_a2a(root, port=8001)` or `adk api_server --a2a`, bound to localhost.
- **Checkpoint UX (v2)**: ADK **`RequestInput`** nodes (resumable over api_server/SSE). CP1 (plan), CP2 (draft), CP3 (mid-acquisition, deep-only). Replaces v1 console `app/checkpoint.py`.
- **A2A boundary**: the whole pipeline is one A2A server (local-first); the 6 specialists stay local nodes on the one hot model (not A2A peers) — A2A overhead buys no concurrency under the RAM limit.
- *(v1, now superseded)*: deterministic stage machine wrapping LLM calls; `SessionStore` as truth + `stage.json` resume; CLI/`$EDITOR` checkpoints. Code still at v1 — migration = architecture.md §15.

## Repo Pointers
- Project root: `C:\Users\samar\PycharmProjects\GoogleADK\LocalGPT_Rebase`
- Existing MCP: `mcp/Crawl4AI_MCP/` (has its own memory-bank/).
- **A2A pipeline: `orchestrator/` (NEW, sibling to mcp/)** — `app/{schemas,config,session}.py`, `main.py`, `scripts/check_setup.py`, `tests/`.
- Skills available: `crawl4ai`, `fastmcp`, `mcp-builder`.

## Orchestrator Package (current state — Stories 1–5 done; v1→v2 migration started, E0+E1+E2.S1 landed)
- Managed with `uv`. `pyproject.toml` deps: **`google-adk[a2a]>=2.3,<3`** (✅ E1.T1; +a2a-sdk 0.3.26 transitive), ollama, pydantic>=2, python-dotenv, httpx, litellm>=1.0, **mcp** (required by ADK `MCPToolset`; added in Story 5).
- **Local venv is Python 3.13** (`orchestrator/.venv`, conda env `LocalGPT_Rebase`).
- Run tests: `uv run pytest -q` (from `orchestrator/`) — **220 orchestrator tests green** (203 + 5 E1 + 13 E0 + 4 E2.S1; was 14 at Story 1); +MCP 43 green.
- Setup probe (G2 — user must run): `python scripts/check_setup.py` — checks Ollama + models + `ollama ps` VRAM.
- Schemas use **Pydantic v2** (`.model_dump_json` / `.model_validate_json` for artifact persistence).
- Key modules: `app/{schemas,config,session,llm,stage_machine,orchestrator,checkpoint,pipeline,jsonio,citation}.py`; `app/agents/{clarifier,planner,acquirer,extractor,verifier,writer}.py`. **v2 shell (E2.S1):** `app/workflow.py` (ADK dynamic `Workflow` + `research` `@node` + `build_research_workflow` DI seam) · `app/adk_app.py` (`App` + `ResumabilityConfig`) · `tests/test_workflow.py`.
- **ADK 2.x dynamic-workflow API anchors (verified against installed 2.3.0, E2.S1):** `from google.adk import Context, Workflow`; `from google.adk.workflow import node, START`; **`from google.adk.events import RequestInput`** (NOT `google.adk.workflow`); `from google.adk.apps import App, ResumabilityConfig`; `from google.adk.runners import InMemoryRunner`. Gotchas: the START-entry node's passthrough param must be named **`node_input`**; `ctx.run_node(...)` returns a node's `BaseModel` output as a **`model_dump()` dict** (coerce via the agents' `parse_*` helpers); resume via `runner.run_async(..., invocation_id=...)`.