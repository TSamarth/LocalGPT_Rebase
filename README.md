# LocalGPT Rebase — A2A Deep Research Pipeline

Local-first, agent-to-agent (A2A) deep research system. Give it a query, it plans, crawls, verifies, and writes a **vetted markdown report** — all inference local via Ollama, only web-fetching touches the network.

## Why

Cloud "research" agents leak your queries, cost per token, and can't be trusted not to hallucinate a single-source claim as fact. This runs on your own GPU/CPU box, verifies every claim against 2+ independent sources (or flags the contradiction), and stops at two human checkpoints so you stay in control of scope and output.

## How it works

```
query → Clarifier (asks only if ambiguous) → Planner → [Checkpoint 1: approve plan]
      → loop per subtopic: Acquirer → Extractor → Verifier   (repeat until stop-rule met)
      → Writer → [Checkpoint 2: approve draft] → data/reports/{session}.md
```

- **Clarifier** — asks follow-ups only on ambiguous scope / missing constraints, otherwise proceeds autonomously.
- **Planner** — breaks query into subtopics, sets depth (shallow/normal/deep) and evidence targets.
- **Acquirer** — discovers + triages source URLs (SerpAPI + DuckDuckGo + arXiv + Semantic Scholar), citation-graph BFS on deep academic queries.
- **Extractor** — crawls/parses pages into SQLite + ChromaDB via the crawl4ai MCP server.
- **Verifier** — the trust core: corroborates claims (≥2 independent sources → kept, contradicted → flagged with confidence score, single source → uncorroborated), flags temporal drift on stale sources.
- **Writer** — renders the final structured markdown (exec summary → per-subtopic sections → sources → contradictions appendix).

Two human checkpoints: approve the plan before research runs, approve the draft before the final report is written. A third (deep-mode only) lets you steer mid-research source lists.

## Architecture

- **Orchestration**: Google ADK 2.x dynamic workflow (`@node`/`Workflow`/`ctx.run_node`). Six specialists run as local in-process nodes on **one shared, role-prompted hot model** — not as separate A2A peers — because the whole pipeline is bounded by 16 GB RAM, not GPU VRAM.
- **A2A boundary**: the entire pipeline is exposed as a single local-first A2A server (`get_fast_api_app(a2a=True)`, localhost:8001) — both REST (`/run_sse`) and the A2A protocol (agent card + RPC) from one process. Consume it directly over HTTP or as a `RemoteA2aAgent` from another ADK agent.
- **Tools**: crawl4ai MCP server (stdio) — the only place with network egress. `Acquirer` gets read tools, `Extractor` gets write tools, `Verifier` gets `search_chunks` — least-privilege, per architecture §2.
- **Sessions/resume**: owned by ADK (`DatabaseSessionService` + `ResumabilityConfig`), resumable by `invocation_id`; a plugin exports plan/ledger/draft to `data/sessions/{id}/*.json` for human inspection.

Full design rationale: [`memory-bank/architecture.md`](memory-bank/architecture.md).

## Hardware envelope (hard constraint)

| Resource | Spec |
|---|---|
| GPU | RTX 4070 Ti Super, 16 GB VRAM |
| CPU | Ryzen 5 3600 |
| RAM | 16 GB — the real bottleneck |
| Models | Qwen2.5-14B-Instruct Q4_K_M (reasoning) + nomic-embed-text (embedding), both resident (`keep_alive=-1`) |

Sequential node execution is structural, not a shortcut — crawling (Playwright) and a resident 14B model can't both run heavy at once under 16 GB RAM.

## Repo layout

```
orchestrator/       ADK pipeline: app/{workflow,adk_app,server,cli,config,schemas,...}.py, app/agents/{clarifier,planner,acquirer,extractor,verifier,writer}.py
mcp/Crawl4AI_MCP/    crawl4ai-backed MCP tool server (discover/triage/crawl/search tools)
memory-bank/         living project docs: projectbrief, productContext, architecture, techContext, progress, requirements
claudedocs/          execution plans / migration logs (gitignored in places — see CLAUDE.md)
```

## Getting started

Prereqs: Ollama running locally with the models above pulled, `uv` for Python dependency management.

```bash
cd orchestrator
uv sync
python scripts/check_setup.py   # verifies Ollama + models + VRAM via `ollama ps`
uv run pytest -q                # offline test suite (no live Ollama needed)
```

Run the pipeline in-process:

```bash
cd orchestrator
uv run python main.py
```

Or serve it as a local A2A/REST agent:

```bash
cd orchestrator
uv run python -m app.server        # localhost:8001 — /run_sse (REST) + A2A card/RPC
uv run python -m app.cli           # thin CLI client driving the server over REST
```

Live acceptance tests (real Ollama, real crawling) are opt-in: `pytest -m live` — see `orchestrator/scripts/acceptance_runbook.md`.

## Status

v1→v2 (ADK 2.x dynamic-workflow) migration is code-complete and the v1 shell has been deleted. A follow-on resource-optimization pass (2026-07-04) shares one crawl4ai MCP subprocess per run across HITL resume hops, holds a persistent crawler process, and adds opt-in session retention pruning. Offline suite: 253 collected / 249 passing orchestrator + 43 MCP tests. All hard gates (resume, parity, byte-comparable export, A2A HITL round-trip) passed. Remaining: user-run live acceptance (`pytest -m live`) on real hardware. See [`memory-bank/progress.md`](memory-bank/progress.md) for full history.

## Docs

- [`memory-bank/projectbrief.md`](memory-bank/projectbrief.md) — scope, requirements, hardware constraints
- [`memory-bank/productContext.md`](memory-bank/productContext.md) — why this exists, UX goals
- [`memory-bank/architecture.md`](memory-bank/architecture.md) — full design, ratified v2
- [`memory-bank/techContext.md`](memory-bank/techContext.md) — stack, design decisions, repo pointers
- [`memory-bank/progress.md`](memory-bank/progress.md) — build history, decision log
- [`CLAUDE.md`](CLAUDE.md) — behavioral guidelines for AI-assisted dev on this repo
