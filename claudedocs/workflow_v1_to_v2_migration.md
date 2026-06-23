# Workflow: v1 (hand-rolled orchestrator) → v2 (ADK 2.x) Migration

> **Status:** Implementation plan (generated 2026-06-23 by `/sc:workflow`). Planning only — no code executed.
> **Input authority:** [memory-bank/architecture.md] §0/§4/§5/§6/§13/§14/**§15** (the migration spec). This doc *re-sequences* §15's 7 design-level steps into executable phases with dependencies, verify gates, file targets, and rollback.
> **Supersedes for execution:** the v1 task breakdowns [workflow_a2a_research.md] / [spawn_a2a_research.md] (those built Stories 1–5; this migrates that code to v2).
> **Next:** `/sc:implement` per phase, in order. Phase 0 is independently shippable today.
>
> **Progress (updated 2026-06-23):** **P0 ✅ done** (T0.1/T0.2/T0.3); live-path follow-ups flagged for P7 (crawl4ai stdout→stderr, agent JSON robustness) also **✅ done** out-of-band (spawn-doc epic E0). Suite now **216 orchestrator + 43 MCP green**. P1–P7 pending. Detail in [spawn_v1_to_v2_migration.md] *Execution Log*.

---

## 0. Migration Invariants (hold across every phase)

These do not change; every phase must preserve them:

- **Reused unchanged:** `app/schemas.py` (artifact contracts), `app/config.py`, `app/citation.py`, `app/jsonio.py`, `app/agents/*` agent *builders*, the pure policy functions (`stop_rule`, `depth_budget`, Verifier independence/confidence/temporal, Writer `coverage_report`/`render_report`, Planner `apply_depth_targets`), crawl4ai MCP (§7), SQLite/Chroma stores.
- **Changed:** only the **orchestration shell** — the driver, the session/resume owner, the checkpoint mechanism, and the external boundary.
- **Idempotency invariant (new, load-bearing):** crawl/extract must stay keyed by URL so ADK's "tools run **at least once** on resume" is safe (§4). Do not add non-idempotent side effects to Extractor/Acquirer tool paths.
- **Sequential-by-RAM invariant:** one hot model, subtopics sequential. Substrate is parallel-ready (`asyncio.gather`, §14) but **do not** enable fan-out — 16 GB RAM is the bottleneck.
- **Test floor:** **216 orchestrator + 43 MCP green** (was 203+43; +5 P0/E1, +13 E0 — 2026-06-23). No phase may land red; v1-driver tests are *retired in lockstep* with the code they cover (Phase 6), not before.
- **AC floor:** AC1–AC7 must pass E2E after each phase that touches a stage. Two new acceptance checks are added: resume-by-`invocation_id` and a localhost A2A round-trip (Phase 7).

---

## 1. Phase Sequence (dependency-ordered)

```
P0 ─┬─▶ P1 ─▶ P2 ─▶ P3 ─┬─▶ P4 ─▶ P5 ─▶ P6 ─▶ P7
    │   (App+CP1 slice)   │   (session flip)
    └─ independent ───────┘
P0 (Tier-0 correctness) can ship NOW, in parallel with nothing blocking it.
P1..P6 are strictly serial (each consumes the prior shell state).
P7 (acceptance) gates "migration done".
```

| Phase | §15 step | Theme | Blocking dep | Risk | Reversible? |
|-------|----------|-------|--------------|------|-------------|
| **P0** | 1 | Tier-0 correctness (env/pin/toolset lifecycle) | none | Low | Yes (small diffs) |
| **P1** | 2 | `App` + one-node `Workflow` + `ResumabilityConfig` (Clarifier→Planner→CP1) | P0 | Med | Yes (parallel-write safety net) |
| **P2** | 3 | Port research loop into `@node` (Acquirer/Extractor/Verifier as `ctx.run_node`) | P1 | High | Yes (v1 driver still present) |
| **P3** | 4 | `RequestInput` checkpoints (CP1/CP2/CP3 + CP3 adapter); retire console `checkpoint.py` | P2 | Med | Partial |
| **P4** | 5 | Flip session ownership → ADK; `SessionStore`→exporter; retire `stage.json` resume | P3 | High | Hard (data-path change) |
| **P5** | 6 | Expose local-first A2A (`to_a2a`/`api_server --a2a`) + agent card + CLI `/run_sse` | P4 | Med | Yes (additive) |
| **P6** | 7 | Retire `orchestrator.py` + `stage_machine.py` driver + `pipeline.py` `_run_sync` | P5 | Med | Hard (deletion) |
| **P7** | accept | AC1–AC7 E2E + resume + A2A round-trip | P6 | — | — |

**Why this order:** correctness fixes first (de-risk the env before any rewrite); then build the new shell *beside* the old one (P1–P3) with `SessionStore` write-through kept as a safety net; only **after** node parity is proven do we cut the source-of-truth over (P4) and remove the old shell (P6). A2A (P5) is additive and could move earlier, but is placed after the session flip so the exposed server already owns resume.

---

## P0 — Tier-0 Correctness (§15.1) — ✅ DONE 2026-06-23

**Goal:** fix the three correctness/alignment defects the alignment review flagged. Independent of the rewrite; ships alone.

| Task | File | Change | Verify | Status |
|------|------|--------|--------|--------|
| T0.1 Pin ADK + a2a extra | `orchestrator/pyproject.toml:7` | `"google-adk>=0.3.0"` → `"google-adk[a2a]>=2.3,<3"`; `uv lock` | `uv sync` resolves; `uv.lock` shows 2.3.x; tests still green | ✅ lock=2.3.0, +`a2a-sdk` 0.3.26, **216 green** |
| T0.2 Set `OLLAMA_API_BASE` | `orchestrator/app/llm.py` (`build_model`) | Set `os.environ["OLLAMA_API_BASE"] = config.OLLAMA_BASE_URL` (env var, **not only** the `api_base=` param — LiteLLM routes non-generation calls through the env var). Keep `api_base=` too. | unit: env var set after `build_model()` | ✅ `test_llm.py` (config.py untouched — not needed) |
| T0.3 MCPToolset `close()` lifecycle | `orchestrator/app/pipeline.py` (`_drive_extractor`, `_make_default_verify_runner`) | `close()` each owned default `MCPToolset` in `try/finally` (was: extractor rebuilds per call, verifier builds lazily per runner call — **leaked, never closed**). Injected toolset left open (caller owns it). | unit (mock toolset): owned closed once / injected never closed | ✅ `test_toolset_lifecycle.py` (4) |

**Acceptance P0:** ✅ `uv sync` clean; full suite green (216); ruff clean; no behavior change beyond lifecycle.

> **T0.3 scope correction (discovered in impl):** the original "build once per run, reuse across passes, close at end" is **infeasible in the v1 shell** — `pipeline._run_sync` runs every handler under its own `asyncio.run`, so a stdio `MCPToolset` (bound to one event loop + subprocess) cannot survive across passes. The *achievable+correct* Tier-0 fix is therefore **close-per-drive-call** (kills the subprocess leak). The single-long-lived-toolset *pattern* moves to **P2 / E2.S2.T4**, where the `@node` workflow runs under one loop and can legitimately build it once per run and close on exit.

---

## P1 — `App` + One-Node `Workflow` Skeleton (§15.2)

**Goal:** stand up the v2 shell on a trivial slice — `Clarifier → Planner → ★CP1` — with ADK-owned resume, **without** touching the research loop. Prove ADK resumability on a thin slice before porting the hard part.

**New files:**
- `orchestrator/app/workflow.py` — `@node async def research(ctx, raw_query)` (slice only: clarify → plan → `yield RequestInput` CP1 → return plan); `root_agent = Workflow(name="deep_research", edges=[("START", research)])`.
- `orchestrator/app/adk_app.py` (or top of `workflow.py`) — `App(name="deep_research", root_agent=root_agent, resumability_config=ResumabilityConfig(is_resumable=True), plugins=[...])`.

**Tasks:**
1. Wrap existing `clarifier`/`planner` *builders* (from `app/agents/*`) as workflow nodes invoked via `await ctx.run_node(agent, input)` — drop the throwaway `Runner`/`InMemorySessionService`/`output_key` extraction (`pipeline.py:_drive` pattern). Parent node sets `rerun_on_resume=True`.
2. CP1 as `yield RequestInput(message="Approve/edit plan", payload=plan, response_schema=ResearchPlan)`.
3. Enable `ResumabilityConfig`; run the slice, kill mid-CP1, resume by `invocation_id`, assert Clarifier/Planner are **skipped** and only CP1 re-runs.
4. **Safety net:** keep `SessionStore` writing plan/clarify through (write-through, parallel) — not yet the truth, but a fallback while P1–P3 stabilize.

**Verify gate:** new `tests/test_workflow.py` — (a) slice runs to CP1 and returns an approved `ResearchPlan`; (b) resume-by-`invocation_id` skips completed nodes; (c) `response_schema` round-trips an edited plan. v1 suite stays green (untouched).

**Risk:** Med — first real ADK 2.x dynamic-workflow wiring; `RequestInput` resume semantics are new. **Mitigation:** trivial slice; v1 path fully intact.

---

## P2 — Port the Research Loop (§15.3)

**Goal:** move the per-subtopic loop into the `@node` workflow; wrap Acquirer/Extractor/Verifier as `ctx.run_node` nodes; reuse policy verbatim.

**Tasks:**
1. Port the `while not stop_rule(...)` loop (architecture.md §4 sketch) into `research()`: iterate `plan.subtopics` sequentially; per pass call `acquirer_node → [CP3 placeholder] → extractor_node → verifier_node`; `merge_ledger`; `iteration += 1`.
2. Wrap Acquirer/Extractor/Verifier builders as nodes; inputs are the typed artifacts (`(subtopic, plan)`, `ScoredURL[]`, `subtopic.question`) — `ctx.run_node` returns the artifact directly (no session extraction).
3. Reuse **verbatim**: `stop_rule`, `depth_budget`, `merge_ledger`, Verifier policy, `coverage_report`. Pre-extract the pure functions from `stage_machine.py` into a policy module if needed so P6 can delete the driver without dragging them — **decide now**: keep `stop_rule`/`depth_budget` importable independent of the `Orchestrator`/`STAGE_ORDER` driver.
4. Carry the P0 toolset-once/close() lifecycle into the node path (build MCPToolset once per workflow run, close on exit — via a plugin/callback or node-scoped context).
5. CP3 left as a **placeholder pass-through** here (real `RequestInput` lands in P3).

**Verify gate:** `test_workflow.py` extended — full INTAKE→draft over an offline/canned model: ledger accumulates across passes; stop-rule terminates each subtopic (target_evidence / diminishing-returns / iteration-cap all exercised); CP3 placeholder is a no-op on shallow/normal and a hook on deep. AC1–AC5 E2E parity vs. v1 pipeline on the same fixture.

**Risk:** High — the loop is the behavioral core; subtle drift vs. v1 ordering/accumulation is the main hazard. **Mitigation:** diff v2 ledger output against v1 `pipeline.run_pipeline` on identical canned inputs (golden-output test). v1 path still runnable for A/B.

---

## P3 — `RequestInput` Checkpoints (§15.4)

**Goal:** replace all three console checkpoints with resumable `RequestInput` nodes; retire console `checkpoint.py` once parity proven.

| Checkpoint | Placement | Payload / schema | Note |
|-----------|-----------|------------------|------|
| CP1 | after Planner (already in P1) | `payload=plan`, `response_schema=ResearchPlan` | approve/edit/reject |
| CP2 | after Writer | `payload=draft`, `response_schema=str` | approve/edit/reject |
| CP3 | after Acquirer, `plan.depth=="deep"` only | `payload=ScoredURLList` | **needs adapter** |
| pre-flight | Clarifier `needs_input`, before run start only | questions schema | optional |

**Tasks:**
1. CP2 + CP3 as `RequestInput` (CP1 done in P1).
2. **CP3 input adapter** (called out in §6/§10): `RequestInput.response_schema` does **not** auto-coerce free-form human input. Port the v1 `cp3_checkpoint` edit grammar (`+add` / `-exclude <n>` / `r redirect` / `d done` → `(filtered, needs_supplemental)`) into a small normalizing adapter (or an agent node) between raw input and the `ScoredURLList` payload. `needs_supplemental` triggers a supplemental Acquirer pass (loop re-entry) before Extractor.
3. Reject path: map `CheckpointRejected` semantics onto workflow abort (resumable at last node).
4. Delete console `app/checkpoint.py` + retire `test_checkpoint.py` **only after** the `RequestInput` equivalents pass parity tests (lockstep).

**Verify gate:** `RequestInput` blocks until response (CP1/CP2); CP3 fires on deep / skipped on shallow+normal; CP3 adapter parses each edit verb incl. supplemental re-entry; reject → resumable abort. Replaces the 11 `test_checkpoint.py` cases with `RequestInput`-level equivalents.

**Risk:** Med — CP3 adapter is the fiddly bit (free-text grammar). **Mitigation:** reuse the v1 grammar + tests almost verbatim; only the I/O surface changes (TTY → `RequestInput` payload).

---

## P4 — Flip Session Ownership to ADK (§15.5)

**Goal:** make the **ADK session + event store** the source of truth; demote `SessionStore` to a write-through exporter; retire `stage.json`-based resume.

**Tasks:**
1. Make ADK session the truth: live stage / current subtopic / budgets / per-node completion live in ADK session; resume driven by `App(resumability_config=...)` + `invocation_id` (the P1 slice already proved this; now it's authoritative).
2. **Demote `SessionStore` → exporter:** a `BeforeAgentCallback`/`AfterAgentCallback` or `App`-level **Plugin** projects ADK state → `data/sessions/{id}/{plan.json,claim_ledger.json,draft.md}` for human inspection/diff/provenance. **Not** in execution overrides (2.0 Workflow Runtime bypasses `_run_async_impl`).
3. Retire `stage.json` as a resume mechanism (keep the export files). Final report still → `data/reports/{session_id}.md`.
4. Remove the P1 "parallel safety-net" writes (now redundant with the exporter).
5. crawl4ai SQLite/Chroma unaffected — still hold content + provenance + `publication_date`.

**Verify gate:** resume-by-`invocation_id` is authoritative (kill mid-loop, resume, only unfinished nodes re-run, ledger intact); export callback produces the three JSON/MD artifacts matching ADK state; `stage.json` no longer read on resume. Tests touching `SessionStore.load_stage`/`stage.json` resume are migrated to the ADK-resume + exporter model.

**Risk:** High — changes the persistence/resume contract; mid-flight resume correctness is critical. **Mitigation:** P1's proven resume slice + golden-output A/B; keep one release where export files are byte-comparable to v1 `SessionStore` output.

---

## P5 — Expose Local-First A2A (§15.6, architecture.md §13)

**Goal:** publish the whole pipeline as **one** localhost A2A server (the project's reason for ADK); wire the thin CLI to it. Additive — no internal A2A.

**Tasks:**
1. Expose via `to_a2a(root_agent, port=8001)` **or** `adk api_server --a2a <folder>` + `agent-card.json` (prefer api_server if it's already the runtime). Bind **localhost**.
2. Agent card at the well-known path (`…/a2a/deep_research/.well-known/agent-card.json`) — skill: "local deep-research → vetted markdown report"; in `text/plain`, out `text/markdown`/`application/json`.
3. Thin CLI speaks `POST /run_sse` (run + stream); `RequestInput` pauses for CP1/CP2/CP3; resume via `invocation_id`. CLI is the local-first consumer.
4. Provide a `RemoteA2aAgent(name="deep_research", agent_card="http://localhost:8001/a2a/deep_research"+AGENT_CARD_WELL_KNOWN_PATH, use_legacy=False)` example for another local ADK agent to consume the pipeline as a sub-agent.
5. **Future split seam only** — do NOT lift any specialist onto its own A2AServer (no concurrency gain on one hot model); just preserve the typed-artifact contracts that make it possible later.

**Verify gate (new acceptance):** localhost A2A round-trip — client `POST /run_sse` → pipeline runs → CP pauses surface over SSE → resume → vetted markdown returned. Agent card fetchable at well-known path. CLI drives a full run over the API.

**Risk:** Med — first real A2A serving + SSE-resumable HITL. **Mitigation:** additive (in-process CLI path from P1–P4 still works); test the API path beside it.

---

## P6 — Retire the v1 Shell (§15.7)

**Goal:** delete the now-dead hand-rolled orchestration once the workflow is the sole driver.

**Delete / repurpose (only after P1–P5 green):**
- `app/orchestrator.py` — the `Orchestrator` deterministic driver → **delete**.
- `app/stage_machine.py` — **split**: keep pure `stop_rule`/`depth_budget`/`ResearchPhase` vocabulary (moved in P2 if needed); delete `STAGE_ORDER`/`next_stage` hand-walk driver bits.
- `app/pipeline.py` — `_run_sync` (`asyncio.run` bridge) + `_drive`/`_drive_extractor` throwaway-Runner machinery → **delete**; composition root role moves to `workflow.py`/`adk_app.py`.
- `app/checkpoint.py` — already deleted in P3.
- Retire v1-driver tests: `test_orchestrator.py`, `test_stage_machine.py` (driver parts), `test_pipeline.py` (pure-policy assertions migrate to `test_workflow.py`; driver assertions deleted).

**Verify gate:** full suite green with the v1 shell gone; no import references `orchestrator.py`/`pipeline._run_sync`; ruff clean; AC1–AC7 + P4 resume + P5 A2A round-trip all green.

**Risk:** Med — deletion; risk is an undiscovered live dependency on the old driver. **Mitigation:** grep for imports before delete; do it last, after every new path is proven.

---

## P7 — Acceptance (Story 6)

**Goal:** prove the migration end-to-end on real hardware.

- AC1–AC7 pass E2E (clarify → plan/CP1 → research loop → verify/independence/confidence/temporal → write/CP2, CP3 deep-only).
- **New:** resume-by-`invocation_id` after a forced mid-loop kill.
- **New:** localhost A2A round-trip (`/run_sse` + agent card + SSE-resumable HITL).
- OOM validation: full run stays inside 16 GB RAM (sequential, one hot model) — `scripts/check_setup.py` (G2) + `ollama ps` VRAM < 14 GB.
- Live-path follow-ups from Story 5 (independent of this migration, but block a clean E2E): crawl4ai MCP stdout banner → stderr; agent JSON robustness under tool timeouts. **✅ Both done 2026-06-23 (spawn-doc epic E0):** fd-level stdout guard in `mcp/Crawl4AI_MCP/main.py`; `jsonio.invoke_json_with_retry` + graceful degrade. Remaining P7 pre-req here = the real-HW run itself (E0.S3, user-gated).

---

## 2. Cross-Cutting Concerns

**Test migration strategy (lockstep, never ahead):**
- New `tests/test_workflow.py` grows P1→P2→P3 (slice → loop → checkpoints).
- v1-driver tests (`test_orchestrator.py`, `test_stage_machine.py` driver, `test_checkpoint.py`, `test_pipeline.py` driver) are retired **only in the phase that deletes their code** (P3 for checkpoints, P6 for the driver). Pure-policy tests (stop-rule, Verifier independence/confidence/temporal, Writer coverage) move, not deleted.
- Golden-output A/B: P2/P4 diff v2 ledger/draft against v1 `run_pipeline` on identical canned inputs — the primary parity guard.

**Rollback posture:**
- P0: trivial revert.
- P1–P3: v1 path fully intact + `SessionStore` write-through safety net; can fall back per-phase.
- P4: hardest to reverse (persistence contract) — gated on a byte-comparable export release.
- P6: irreversible deletion — last, after everything green.

**Effort shape (relative):** P0 small · P1 medium · **P2 large** · P3 medium · **P4 large** · P5 medium · P6 small · P7 medium (hardware-gated). P2 and P4 are the two hard phases; everything else is mechanical or additive.

**What this migration does NOT touch:** artifact schemas, config, citation BFS, MCP server + its 6 extensions, SQLite/Chroma, the Verifier trust policy, the Writer skeleton, depth budgets. Only the shell moves.

---

## 3. Handoff to `/sc:implement`

Recommended first slice: **P0 (all three tasks) + P1 (App + one-node Workflow + CP1 + resume validation).** P0 ships value immediately and de-risks the env; P1 proves ADK-owned resume on a trivial slice before the high-risk P2 loop port. Stop and validate resume-by-`invocation_id` at the P1 gate before proceeding.

> **Done so far (2026-06-23):** P0 ✅ + live-path hardening (E0) ✅. **Next slice = P1** (`app/workflow.py` + `app/adk_app.py`, Clarifier→Planner→CP1 `RequestInput`, `ResumabilityConfig`, resume-by-`invocation_id` validation) — the P1 gate is a hard stop-and-verify.
