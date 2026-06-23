# Task Hierarchy: v1 → v2 (ADK 2.x) Migration

> **Status:** Task decomposition (generated 2026-06-23 by `/sc:spawn`). Planning only — no code executed.
> **Source:** [workflow_v1_to_v2_migration.md] (phases P0–P7) ← [memory-bank/architecture.md] §15.
> **Purpose:** Epic → Story → Task breakdown with explicit **‖ PARALLEL** vs **→ SEQUENTIAL** markers, dependencies, and `/sc:*` delegation. Execute via the delegated commands in dependency order.
>
> **Execution status (updated 2026-06-23):** **E0 ✅ done** (S1/S2 landed; S3 ⏳ user-run) · **E1 ✅ done** (T1/T2/T3) · E2–E5 pending. Suite now **216 orchestrator + 43 MCP green** (was 203+43). Per-task ✅ markers inline below; details in the *Execution Log* at the end. Working tree only — not yet committed.

---

## Legend

- **‖ PARALLEL** — no dependency on a sibling in the same group; can run concurrently (by separate agents/worktrees or interleaved).
- **→ SEQUENTIAL** — must follow the named predecessor; consumes its output/shell state.
- **Delegate:** the `/sc:*` command that executes the item.
- **Gate:** the verify condition that must pass before dependents start.

**Coordination strategy = ADAPTIVE:** the migration *spine* (E1→E2→E3→E4→E5) is strictly sequential (each consumes the prior orchestration shell). Parallelism exists **within** epics and on **one fully independent side-track** (E0, live-path hardening). Single-developer note: even where tasks are ‖, the 16 GB box runs one live model — *build* parallelism is fine, *live E2E* runs serialize.

---

## Dependency Spine (epic level)

```
        E0  (live-path hardening) ───────────────────── ‖ independent, joins at E5/P7 ──┐
                                                                                         ▼
E1 (P0) ──→ E2 (P1,P2) ──→ E3 (P3,P4) ──→ E4 (P5) ──→ E5 (P6,P7)
  ‖ internal    → serial        → serial      additive     → serial
```

- **E1 → E2 → E3 → E4 → E5**: SEQUENTIAL spine. No reordering (shell state hand-off).
- **E0**: PARALLEL to E1–E3 entirely (touches MCP server + agent prompts, not the shell). Must land before E5/P7 green.

---

## E0 — Epic: Live-Path Hardening (parallel side-track)

**Independent of the shell migration.** Fixes the two Story-5 live blockers so P7 E2E can pass. Touches `mcp/Crawl4AI_MCP/` + agent prompts — **disjoint from `orchestrator/app/` shell files** → safe to run alongside E1–E3.

| ID | Story / Task | Mode | Delegate | Gate |
|----|--------------|------|----------|------|
| **E0.S1** ✅ | crawl4ai MCP stdout→stderr (banner corrupts JSON-RPC line under stdio) | ‖ PARALLEL (vs E0.S2, vs E1–E3) | `/sc:troubleshoot` → `/sc:implement` | ✅ **fd-level guard** in `mcp/Crawl4AI_MCP/main.py` (`dup2(2,1)` + `sys.stdout` rebind over saved fd); MCP **43 green** |
| **E0.S2** ✅ | Agent JSON robustness under tool timeouts (retry/repair or structured 2nd pass for Acquirer/Verifier) | ‖ PARALLEL (vs E0.S1, vs E1–E3) | `/sc:implement` → `/sc:test` | ✅ `jsonio.invoke_json_with_retry` (1 JSON-only re-ask) + degrade-to-empty on `ValueError`; canned-timeout tests |
| **E0.S3** ⏳ | Run setup probe on real HW (pull `qwen2.5:14b-instruct-q4_K_M` + embed; VRAM<14 GB) | ‖ PARALLEL — **user-run**, anytime before P7 | `/sc:test` (manual `scripts/check_setup.py`) | ⏳ **pending user** — G2: models present, `ollama ps` VRAM < 14 GB |

> E0.S1 ‖ E0.S2 ‖ E0.S3 — all three concurrent. None block E1.

---

## E1 — Epic: Foundation & Correctness (P0)

**Goal:** Tier-0 correctness; ships standalone today. **All three tasks ‖ PARALLEL** (different files, no shared surface).

| ID | Task | Mode | File | Delegate | Gate |
|----|------|------|------|----------|------|
| **E1.T1** ✅ | Pin `google-adk[a2a]>=2.3,<3` + `uv lock` | ‖ PARALLEL | `pyproject.toml:7` | `/sc:implement` | ✅ `uv sync` clean; lock=**2.3.0** (+`a2a-sdk` 0.3.26); **216 green** |
| **E1.T2** ✅ | Set `OLLAMA_API_BASE` env var (keep `api_base=`) | ‖ PARALLEL | `app/llm.py` | `/sc:implement` | ✅ env set in `build_model()`; `test_llm.py` |
| **E1.T3** ✅ | MCPToolset `close()` per drive call (extractor + verifier paths) | ‖ PARALLEL | `app/pipeline.py` (`_drive_extractor`, `_make_default_verify_runner`) | `/sc:implement` | ✅ owned toolset `close()`d once / injected left open; 4 tests. **Scope correction:** "once across N passes" is **infeasible in v1's `asyncio.run`-per-handler bridge** (stdio subprocess bound to one loop) → single-long-lived toolset deferred to **E2.S2.T4** (node path) |

**Epic gate (E1 done):** ✅ `uv sync` clean · full suite green (216) · ruff clean · no behavior change beyond lifecycle. **→ E2 unblocked.**

> Merge order note: E1.T1/T2/T3 are ‖ to *write* but share `pyproject`/lockfile churn on merge — land E1.T1 (lock) last to avoid re-resolves.

---

## E2 — Epic: v2 Shell Core (P1 → P2)  [SEQUENTIAL spine]

**Goal:** stand up `App` + dynamic `Workflow`; port the research loop. **Depends on E1.** Internally mostly serial (loop consumes the skeleton).

### E2.S1 — Story: App + One-Node Workflow skeleton (P1)  → SEQUENTIAL (after E1)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E2.S1.T1 | New `app/workflow.py` — `@node research` slice (clarify→plan→CP1) + `Workflow(edges=[("START",research)])` | → after E1 | `/sc:implement` | slice runs to CP1, returns approved `ResearchPlan` |
| E2.S1.T2 | New `app/adk_app.py` — `App(..., resumability_config=ResumabilityConfig(is_resumable=True))` | → after T1 (can co-dev) | `/sc:implement` | App boots; root_agent wired |
| E2.S1.T3 | Wrap clarifier/planner builders as `ctx.run_node` (drop throwaway Runner); `rerun_on_resume=True` | → after T1 | `/sc:implement` | no `_drive` Runner in slice path |
| E2.S1.T4 | CP1 `RequestInput(response_schema=ResearchPlan)` | → after T3 | `/sc:implement` | edited plan round-trips |
| E2.S1.T5 | Resume validation: kill mid-CP1, resume by `invocation_id`, assert nodes skipped | → after T4 | `/sc:test` | **resume gate** — only CP1 re-runs |
| E2.S1.T6 | Safety net: keep `SessionStore` write-through (parallel) | ‖ with T3-T5 | `/sc:implement` | plan/clarify still on disk |

**Story gate:** `test_workflow.py` (a)(b)(c) green; v1 suite untouched-green. **Stop here, validate resume before E2.S2.**

### E2.S2 — Story: Port Research Loop (P2)  → SEQUENTIAL (after E2.S1)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E2.S2.T1 | **Decide + extract** pure policy (`stop_rule`/`depth_budget`) into import-independent module (un-couple from `Orchestrator`/`STAGE_ORDER` before P6 deletes driver) | → first in S2 (blocks T2) | `/sc:design` → `/sc:implement` | policy importable w/o driver; tests pass |
| E2.S2.T2 | Port `while not stop_rule(...)` loop into `research()`; sequential subtopics; merge_ledger | → after T1 | `/sc:implement` | loop accumulates ledger |
| E2.S2.T3 | Wrap Acquirer/Extractor/Verifier as `ctx.run_node` nodes (typed artifact in/out) | ‖ with T2 (co-dev), join at T4 | `/sc:implement` | nodes return artifacts, no session extraction |
| E2.S2.T4 | Carry P0 toolset-once/close() into node path (plugin/node-scoped ctx) | → after T3 | `/sc:implement` | toolset built once/run, closed |
| E2.S2.T5 | CP3 placeholder pass-through (real RequestInput in E3) | → after T2 | `/sc:implement` | no-op shallow/normal, hook on deep |
| E2.S2.T6 | **Golden-output A/B** vs v1 `run_pipeline` on canned inputs | → after T2-T4 | `/sc:test` | v2 ledger == v1 ledger (parity) |

**Epic gate (E2 done):** full INTAKE→draft offline; stop-rule all 3 exits exercised; AC1–AC5 parity vs v1. **→ unblocks E3.** **High risk — parity test (T6) is the guard.**

---

## E3 — Epic: HITL + Session Ownership (P3 → P4)  [SEQUENTIAL spine]

**Goal:** real `RequestInput` checkpoints; flip session truth to ADK. **Depends on E2.**

### E3.S1 — Story: RequestInput Checkpoints (P3)  → SEQUENTIAL (after E2)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E3.S1.T1 | CP2 `RequestInput(payload=draft, response_schema=str)` after Writer | ‖ with T2 | `/sc:implement` | CP2 blocks until response |
| E3.S1.T2 | **CP3 input adapter** — port v1 `+add/-exclude/r/d` grammar → normalize free-text → `ScoredURLList`; `needs_supplemental` → Acquirer re-entry | ‖ with T1 | `/sc:implement` | each verb parsed; supplemental re-entry works |
| E3.S1.T3 | Reject path → workflow abort (resumable at last node) | → after T1,T2 | `/sc:implement` | reject = resumable abort |
| E3.S1.T4 | Delete console `checkpoint.py` + retire `test_checkpoint.py` (**lockstep, after parity**) | → after T1-T3 | `/sc:cleanup` | RequestInput equivalents green first |

**Story gate:** CP1/CP2 block; CP3 deep-only + adapter verbs; reject resumable. **(CP3 adapter = fiddly bit.)**

### E3.S2 — Story: Flip Session Ownership (P4)  → SEQUENTIAL (after E3.S1)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E3.S2.T1 | ADK session = source of truth (stage/subtopic/budgets/completion in ADK session) | → first in S2 | `/sc:implement` | resume driven by ADK, not stage.json |
| E3.S2.T2 | Demote `SessionStore` → exporter (After-callback / App Plugin → `data/sessions/{id}/*.json`); NOT execution override | → after T1 | `/sc:implement` | export artifacts match ADK state |
| E3.S2.T3 | Retire `stage.json` resume; remove P1 parallel safety-net writes | → after T2 | `/sc:cleanup` | stage.json not read on resume |
| E3.S2.T4 | Migrate `SessionStore.load_stage`/stage.json-resume tests → ADK-resume + exporter model | ‖ with T2-T3 | `/sc:test` | tests green on new model |
| E3.S2.T5 | Authoritative resume validation: kill mid-loop, resume, ledger intact | → after T1-T3 | `/sc:test` | **resume-by-invocation_id authoritative** |

**Epic gate (E3 done):** ADK owns resume; exporter produces 3 artifacts byte-comparable to v1 (one release); AC1–AC7 hold. **→ unblocks E4. High risk — persistence contract.**

---

## E4 — Epic: Local-First A2A Exposure (P5)  [additive, after E3]

**Goal:** publish whole pipeline as one localhost A2A server + CLI. **Additive** (in-process path still works) → lower risk; some internal ‖.

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| **E4.T1** | Expose via `to_a2a(root, port=8001)` **or** `adk api_server --a2a`; bind localhost | → first (blocks T2-T4) | `/sc:implement` | server boots on localhost |
| **E4.T2** | Agent card at well-known path (skill, in `text/plain`, out `text/markdown`) | ‖ with T3 (after T1) | `/sc:implement` | card fetchable at `.well-known/agent-card.json` |
| **E4.T3** | Thin CLI → `POST /run_sse` (stream + RequestInput pause + resume by `invocation_id`) | ‖ with T2 (after T1) | `/sc:implement` | CLI drives full run over API |
| **E4.T4** | `RemoteA2aAgent` consumption example (local sub-agent) | ‖ with T2,T3 (after T1) | `/sc:document` | example consumes pipeline |
| **E4.T5** | A2A round-trip test (run_sse → CP pause over SSE → resume → markdown) | → after T1-T3 | `/sc:test` | **new acceptance: A2A round-trip** |

**Epic gate (E4 done):** localhost A2A round-trip green; CLI over API works; card published. **→ unblocks E5.** **Do NOT split specialists onto own A2A — seam only.**

---

## E5 — Epic: Cleanup + Acceptance (P6 → P7)  [SEQUENTIAL, last]

**Goal:** delete dead v1 shell; prove migration E2E on real HW. **Depends on E4 + E0.**

### E5.S1 — Story: Retire v1 Shell (P6)  → SEQUENTIAL (after E4)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E5.S1.T1 | Grep all imports of `orchestrator.py` / `pipeline._run_sync` (pre-delete safety) | → first (blocks deletes) | `/sc:analyze` | zero live refs outside tests |
| E5.S1.T2 | Delete `app/orchestrator.py` (Orchestrator driver) | → after T1 | `/sc:cleanup` | suite green |
| E5.S1.T3 | Split `app/stage_machine.py` — keep policy/`ResearchPhase`, delete `STAGE_ORDER`/`next_stage` driver | → after T1 | `/sc:cleanup` | policy intact, driver gone |
| E5.S1.T4 | Delete `pipeline.py` `_run_sync`/`_drive`/`_drive_extractor` machinery | → after T1 | `/sc:cleanup` | suite green |
| E5.S1.T5 | Retire v1-driver tests (`test_orchestrator`, `test_stage_machine` driver, `test_pipeline` driver); migrate pure-policy tests | ‖ with T2-T4 | `/sc:test` | only dead-code tests removed |

> E5.S1.T2 ‖ T3 ‖ T4 once T1 clears (different files); T5 tracks alongside.

**Story gate:** v1 shell gone; no dead imports; ruff clean; suite green.

### E5.S2 — Story: Acceptance (P7 / Story 6)  → SEQUENTIAL (after E5.S1 + E0)

| ID | Task | Mode | Delegate | Gate |
|----|------|------|----------|------|
| E5.S2.T1 | AC1–AC7 E2E on real HW | → needs E0 done | `/sc:test` | all AC pass |
| E5.S2.T2 | Resume-by-`invocation_id` after forced mid-loop kill | ‖ with T3 | `/sc:test` | resume correct |
| E5.S2.T3 | Localhost A2A round-trip E2E | ‖ with T2 | `/sc:test` | round-trip green |
| E5.S2.T4 | OOM validation — full run < 16 GB RAM, VRAM < 14 GB | → after T1 | `/sc:test` | no OOM; G2 |

**Epic gate (E5 / MIGRATION DONE):** AC1–AC7 + resume + A2A + OOM all green; v1 shell deleted; E0 hardening merged.

---

## Parallelization Summary

**Can run concurrently:**
- **E0 entirely ‖ E1–E3** (MCP/agent-prompt files disjoint from shell). Biggest parallel win — assign E0 to a second agent/worktree on day 1.
- **Within E1:** T1 ‖ T2 ‖ T3 (3 independent files).
- **Within E2.S2:** loop port (T2) ‖ node wrapping (T3) after policy extraction (T1).
- **Within E3.S1:** CP2 (T1) ‖ CP3 adapter (T2).
- **Within E4:** agent-card (T2) ‖ CLI (T3) ‖ RemoteA2aAgent example (T4) after server stands up (T1).
- **Within E5.S1:** the three deletes (T2/T3/T4) ‖ after import-grep (T1).
- **Within E5.S2:** resume (T2) ‖ A2A round-trip (T3).

**Strictly sequential (no reorder):**
- Epic spine **E1 → E2 → E3 → E4 → E5** (orchestration-shell hand-off).
- **E2.S1 → E2.S2** (loop needs skeleton); **E3.S1 → E3.S2** (session flip needs RequestInput in place); resume-gate checkpoints (E2.S1.T5, E3.S2.T5) block their dependents.

**Hard-gated checkpoints (stop-and-verify):**
1. After **E2.S1.T5** — ADK resume proven on slice before loop port.
2. After **E2.S2.T6** — golden-output parity before checkpoints/session flip.
3. After **E3.S2** — byte-comparable export before deleting v1 (E5).

**Single-box caveat:** ‖ applies to *implementation/build*. Live E2E (E5.S2, E0.S3) serialize on the one hot model + 16 GB RAM.

---

## Delegation Roll-Up

| Epic | Primary commands | Risk | Parallel opportunity |
|------|------------------|------|----------------------|
| E0 | `/sc:troubleshoot`, `/sc:implement`, `/sc:test` | Med | **Full ‖ side-track** |
| E1 | `/sc:implement` | Low | 3 ‖ tasks |
| E2 | `/sc:design`, `/sc:implement`, `/sc:test` | **High** | partial (loop ‖ nodes) |
| E3 | `/sc:implement`, `/sc:cleanup`, `/sc:test` | **High** | partial (CP2 ‖ CP3) |
| E4 | `/sc:implement`, `/sc:document`, `/sc:test` | Med | high (card ‖ CLI ‖ example) |
| E5 | `/sc:analyze`, `/sc:cleanup`, `/sc:test` | Med | deletes ‖ after grep |

**Recommended kickoff:** start **E0 (side-track)** and **E1 (all 3 ‖)** simultaneously. E1 closes fast → begin E2.S1; E0 runs in background until E5 acceptance.

---

## Execution Log

### 2026-06-23 — E0 + E1 executed in parallel (kickoff slice) ✅
Ran exactly the recommended kickoff: **E1** (spine, in main thread) + **E0** (live-path hardening, background agent) concurrently on the same working tree (disjoint files, no conflict).

**E1 — Foundation & Correctness (P0):**
- **E1.T1** — `pyproject.toml` `google-adk>=0.3.0` → `google-adk[a2a]>=2.3,<3`; `uv lock`+`uv sync`. Resolved cleanly; **a2a-sdk 0.3.26** (+google-api-core/proto deps) pulled; lock pins 2.3.0 (already the installed version, so zero v1-code behavior change).
- **E1.T2** — `app/llm.py` `build_model()` now sets `os.environ["OLLAMA_API_BASE"] = config.OLLAMA_BASE_URL` (mirrors `api_base=` so LiteLLM's non-generation paths reach the same Ollama). `config.py` untouched (not needed). +`tests/test_llm.py`.
- **E1.T3** — `app/pipeline.py` `_drive_extractor` + `_make_default_verify_runner` now build the default `MCPToolset` explicitly and `close()` it in `try/finally` (kills the per-pass stdio-subprocess leak from the Story-5 live smoke); an *injected* toolset is left open (caller owns it). +`tests/test_toolset_lifecycle.py` (4). **Deferred:** true once-per-run (no respawn) → E2.S2.T4, because v1's per-handler `asyncio.run` cannot share one stdio toolset across loops.

**E0 — Live-Path Hardening (background agent):**
- **E0.S1** — root cause = crawl4ai `AsyncWebCrawler` prints its INIT banner via a `rich.Console()` defaulting to stdout. Fix = fd-level `_guard_stdio()` in `mcp/Crawl4AI_MCP/main.py`: `dup2(2,1)` repoints fd-1 (banner/print/C-level) to stderr, `sys.stdout` rebound over the saved real-stdout fd so MCP keeps a clean JSON-RPC channel; root logger → stderr. **MCP 43 green.**
- **E0.S2** — `jsonio.invoke_json_with_retry` (+`has_json`, `JSON_ONLY_REASK`): one JSON-only corrective re-ask, then graceful degrade (`acquire→[]`, `verify→empty ledger`) on `loads_first_json` `ValueError`; genuine Pydantic `ValidationError` still surfaces. Wired into `acquirer.acquire`/`verifier.verify`. +tests in `test_jsonio.py`/`test_acquirer.py`/`test_verifier.py`.
- **E0.S3** — ⏳ **pending user**: `cd orchestrator && python scripts/check_setup.py` (pull `qwen2.5:14b-instruct-q4_K_M` + embed, confirm VRAM < 14 GB, G2).

**Integrated verification:** full tree **216 orchestrator + 43 MCP green, ruff clean**, additive-only diff (no baseline tests dropped). Not committed (awaiting user).
