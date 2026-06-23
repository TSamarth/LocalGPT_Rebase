# ADK Layer Alignment Review

> **Status (2026-06-23): RATIFIED INTO DESIGN.** These findings are adopted as the **v2 design** in [`../memory-bank/architecture.md`](../memory-bank/architecture.md) (§0 decisions, §13 A2A, §14 ADK 2.x runtime, §15 v1→v2 migration). User decisions: commit to **ADK 2.x**; **ADK-owned sessions** (`data/sessions/*.json` → export); **A2A** at the pipeline edge, **local-first**; deploy via **`adk api_server`**; orchestration via **dynamic workflows**. This file is the input review; architecture.md is now the authority.

**Date:** 2026-06-23
**Scope:** `orchestrator/app/**` (the ADK layer) vs. the current Google ADK docs (`adk.dev`, ADK Python **2.x**).
**Method:** Read the implemented ADK layer in full, then cross-checked against live ADK documentation (workflow runtime, dynamic workflows, resume, human-input, Ollama, MCP tools) fetched via the ADK docs MCP.

---

## 0. The headline: you are already on ADK 2.0, but coding like 1.x

| Fact | Evidence |
|------|----------|
| Declared floor | `orchestrator/pyproject.toml`: `google-adk>=0.3.0` |
| Actually resolved/locked | `uv.lock`: **`google-adk 2.3.0`** (uploaded 2026-06-18) |
| ADK 2.0 GA | 2026-05-19 — Workflow Runtime, graph + dynamic workflows, native resume + HITL |

ADK 2.0 replaced the *hierarchical agent executor* with a **graph-based Workflow Runtime** (`BaseAgent` now subclasses `BaseNode`). The codebase predates that model: it hand-rolls an orchestrator, a stage machine, a sync↔async bridge, a file-based resume store, and console human-checkpoints — all of which ADK 2.0 now provides as first-class primitives.

Nothing here is *broken* (you override no ADK execution internals, so the 2.0 engine doesn't fight you). But you are maintaining a large amount of infrastructure that ADK now owns, and the dependency floor (`>=0.3.0`) is dangerously loose — it spans the 1.x→2.0 breaking change. **Pin it.**

> **Net recommendation:** treat this as two workstreams — (A) small correctness/alignment fixes you should do regardless, and (B) a strategic decision about whether to adopt ADK 2.0 *dynamic workflows* and retire the hand-rolled orchestration layer.

---

## 1. Current architecture (as built)

Six agents, all the **same** Ollama model (`qwen2.5:14b`) via `LiteLlm`, differentiated only by role prompt (`app/llm.py:build_agent`):

- **Reasoning-only** (Clarifier, Planner, Writer, default Verifier): `output_schema=<Pydantic>`, no tools.
- **Tool agents** (Acquirer, Extractor, Verifier-with-search): hold an `MCPToolset` (crawl4ai stdio server), `tool_filter` for least-privilege, `output_schema=None`.

Orchestration is **entirely outside ADK**:

- `app/orchestrator.py` — a deterministic driver over a linear `Stage` enum; owns all transitions.
- `app/stage_machine.py` — `STAGE_ORDER`, `next_stage`, plus an inner research loop (`ResearchPhase`: acquire→[mid-acquire]→extract→verify) with a pure `stop_rule` + depth budget.
- `app/pipeline.py` — composition root; each handler bridges async→sync with `asyncio.run` (`_run_sync`) and spins up a **throwaway `Runner` + `InMemorySessionService` per agent call**, reads the agent's `output_key` out of session state, then discards the session.
- `app/session.py` — custom file persistence (`stage.json`, `plan.json`, `claim_ledger.json`, …) = the real cross-agent data bus and resume mechanism.
- `app/checkpoint.py` — CP1/CP2/CP3 human gates via `input()`/stdin/`$EDITOR`.

**What's genuinely good and should NOT be churned:**

- Clean **model/policy split** (LLM judges content; deterministic Python owns policy — Verifier independence/temporal-drift, Planner target_evidence, Writer coverage). This is exactly the right instinct and ADK-agnostic.
- **Least-privilege `tool_filter`** per agent (read vs. crawl tools) — matches ADK MCP guidance.
- `ollama_chat/` provider prefix (not `ollama`) — matches the ADK Ollama doc's explicit warning.
- **Injectable runners** for offline tests — good design; portable to any orchestration model.

---

## 2. Findings & recommendations

### Tier 0 — Correctness / do regardless of strategy

#### 0.1 `OLLAMA_API_BASE` env var is not set (latent connectivity bug)
`app/llm.py:build_model` passes `api_base=config.OLLAMA_BASE_URL` to `LiteLlm`. The ADK Ollama doc is explicit:

> "Although you can specify the `api_base` parameter in LiteLLM for generation, as of v1.65.5 the library relies on the **environment variable** for other API calls. Therefore, you should set the `OLLAMA_API_BASE` environment variable … to ensure all requests are routed correctly."

You're on litellm 1.89.3, well past 1.65.5. Today it works only because your base equals the default `http://localhost:11434`. The moment someone sets `OLLAMA_BASE_URL` to a remote/non-default host, non-generation calls silently hit localhost.
**Action:** set `os.environ["OLLAMA_API_BASE"] = config.OLLAMA_BASE_URL` at startup (and keep the `api_base` param). One line in `config.py` or `main.py`.

#### 0.2 Dependency floor spans the 1.x→2.0 breaking change
`google-adk>=0.3.0` permits everything from ancient pre-1.0 through 2.x. The 2.0 release notes list breaking changes (Event schema `node_info`/`output`, `BaseAgent`→`BaseNode`, no direct event append, don't catch `BaseException`).
**Action:** pin intentionally — `google-adk>=2.3,<3` (or `~=2.3`). Decide *deliberately* which major you support; don't let the resolver decide.

#### 0.3 MCP toolset / subprocess churn in the research loop
Every `build_*` for a tool agent calls `_build_default_toolset()`, which constructs a **fresh `MCPToolset`** → a **new crawl4ai stdio subprocess** + a `list_tools` handshake. In the live loop:
- `pipeline._drive_extractor` builds a new Extractor (new toolset/subprocess) **every extract phase**.
- `pipeline._make_default_verify_runner` builds a new toolset+Verifier **every verify phase**.

For a deep plan (≤6 passes × N subtopics) that's dozens of subprocess spawns + MCP handshakes, none explicitly closed (you rely on process exit; the ADK MCP doc notes the toolset "handles graceful shutdown when the agent or application terminates" — i.e. it expects a managed lifecycle, not abandonment).
**Action:** construct each MCP toolset **once** and reuse it across passes (inject it through `PipelineDeps`, which you already have the seam for). Explicitly `await toolset.close()` at run end. This alone should noticeably cut latency on deep runs.

### Tier 1 — ADK alignment quick wins (low risk)

#### 1.1 Stop rebuilding agents per call; reuse session state as the bus *within* a stage
Each handler creates `InMemorySessionService()`, a `Runner`, one session, runs once, extracts `output_key`, discards. That's the heaviest possible way to call an agent and it throws away ADK's actual cross-agent mechanism (shared `session.state`, `output_key` chaining). You currently re-implement that bus in `session.py`.
You don't have to abandon your file store (it's your durable resume artifact), but **building a Runner + session per single agent turn** is pure overhead. Cache built agents; reuse one session service per run.

#### 1.2 Callbacks/plugins instead of bespoke glue
ADK 2.0 wants cross-cutting logic in `BeforeAgentCallback`/`AfterAgentCallback` (and **Plugins** at the `App` level), *not* in execution overrides. You have no structured logging/telemetry today. If you add observability (you'll want it for a multi-pass research loop), do it via callbacks/plugins — they survive the 2.0 engine; ad-hoc wrappers around `Runner.run` do not.

#### 1.3 Let exceptions propagate to ADK's retry machinery
ADK 2.0 added native `RetryConfig(max_attempts=…)` and automatic retry. The migration note is blunt: a broad `except Exception:` inside a tool **disables** 2.0 retries, and catching `BaseException` breaks HITL pause (`NodeInterruptedError`). Audit `citation.py` / any tool-side try/except and `pipeline.py` — let standard exceptions bubble; never catch `BaseException`.

### Tier 2 — Strategic: adopt ADK 2.0 *dynamic workflows* (the big one)

This is the decision that matters. **ADK 2.0 dynamic workflows are a near-exact fit for what you hand-rolled**, and would let you delete a lot of code. Mapping:

| Your code today | ADK 2.0 dynamic-workflow equivalent |
|---|---|
| `orchestrator.py` driver + `_run_sync` bridge | `Workflow` + `@node` async functions; `await ctx.run_node(agent_or_fn, input)` returns output directly — no asyncio.run, no manual session extraction |
| `stage_machine.STAGE_ORDER` linear walk | a sequence of `await ctx.run_node(...)` calls in one `@node` workflow fn |
| research loop `while not stop_rule(...)` | a real `while` loop inside a `@node` (docs show exactly this: generate→check→fix loop) |
| `session.py` resume / "skip completed stages" | **automatic checkpointing** — "successful sub-nodes are automatically skipped when resuming"; enable via `App(resumability_config=ResumabilityConfig(is_resumable=True))` |
| `checkpoint.py` CP1/CP2/CP3 `input()`/stdin | `yield RequestInput(message=…, payload=…, response_schema=…)` — pauses the workflow for human input, resumable via API/UI, no terminal-bound stdin |
| per-subtopic loop you run serially | `asyncio.gather(*[ctx.run_node(worker, st) for st in subtopics])` — parallel subtopics, and resume re-runs only failed workers |

**What you gain:** durable resume for free (replaces `session.py`'s bespoke logic), HITL that works over API/web (not just a local TTY), parallel subtopic research, and far less orchestration code to own. Data passing gets simpler — `run_node()` returns the node output, so you stop hand-extracting `output_key` from discarded sessions.

**What to weigh (honest cost):**
- It's a real rewrite of `orchestrator.py` + `pipeline.py` + `checkpoint.py`, and a rethink of `session.py`'s role (your file artifacts may become a *projection* of ADK's event/session store rather than the source of truth).
- Your deterministic policy functions (`stop_rule`, Verifier/Writer/Planner policy) port **unchanged** — they're pure and become `@node` function-nodes or inline calls. That's the low-risk majority of your logic.
- HITL `response_schema` does **not** auto-coerce free-form human input (doc caveat) — your CP3 URL-edit grammar would need a small adapter or an agent node to normalize.
- Resume caveat: tools may run **more than once** on resume ("at least once"). Your crawl/extract is effectively idempotent (content lands in Chroma keyed by URL), but verify that before relying on it.

**Suggested sequencing if you go this route:**
1. Land Tier 0 + Tier 1 first (they're valuable on their own and de-risk the env).
2. Prototype one vertical slice as a dynamic workflow: `CLARIFY → PLAN → CP1` using `@node` + `RequestInput`, with the existing agents dropped in via `ctx.run_node`. Keep the file store as a write-through projection.
3. Port the research loop (`while` + `stop_rule`) and subtopic fan-out.
4. Move resume onto `ResumabilityConfig`; demote `session.py` to artifact export.

### Tier 3 — Naming / future direction note

The project is named **A2A** (agent-to-agent) and `claudedocs/` carries A2A research, but the implementation is a single in-process pipeline — there is no ADK **A2A protocol** usage (no agents exposed/consumed over A2A). That's fine for a local pipeline, but if "A2A" implies a future where Acquirer/Verifier/etc. run as independently deployable agents, ADK 2.0's A2A (`adk.dev/a2a/`) is the supported path and would change how you draw the boundaries now. Flag for a deliberate decision; don't let the name imply an architecture you haven't built.

---

## 3. Quick-reference action list

| # | Action | Effort | Risk | Tier |
|---|--------|--------|------|------|
| 0.1 | Set `OLLAMA_API_BASE` env var from config | trivial | none | correctness |
| 0.2 | Pin `google-adk>=2.3,<3` | trivial | none | correctness |
| 0.3 | Build each MCP toolset once, reuse, `close()` at end | small | low | correctness/perf |
| 1.1 | Cache built agents; one session service per run | small | low | alignment |
| 1.2 | Observability via callbacks/plugins (when added) | medium | low | alignment |
| 1.3 | Audit try/except; never catch `BaseException` | small | low | alignment |
| 2.x | Migrate orchestration to ADK 2.0 dynamic workflows | large | medium | strategic |
| 3.x | Decide whether "A2A" means real ADK A2A boundaries | n/a | n/a | direction |

---

## 4. Open questions for you

1. **Target ADK major** — commit to 2.x (recommended; it's what's locked) and pin accordingly?
2. **Appetite for the Tier-2 rewrite** — adopt dynamic workflows now, or bank Tier 0/1 and revisit? (My rec: do Tier 0/1 now; prototype one Tier-2 slice before committing.)
3. **Resume source of truth** — are you willing to let ADK's session/event store own resume, with your `data/sessions/*.json` becoming an export? Or must the file layout stay authoritative?
4. **Deployment horizon** — local-CLI only, or eventually `adk api_server` / web / Agent Runtime? (HITL-over-API and resume-over-API only pay off if you leave the pure-TTY model.)
5. **"A2A" intent** — real cross-agent protocol boundaries later, or just a project codename?

---

### Docs consulted
ADK 2.0 overview & breaking changes; Workflow agents (superseded notice); Dynamic workflows; Resume stopped agents; Human input for workflows; Ollama model host; MCP tools — all from `adk.dev` (ADK Python 2.x), fetched 2026-06-23.
