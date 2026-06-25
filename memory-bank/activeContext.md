# Active Context

## Current Phase — v1→v2 MIGRATION IN PROGRESS (2026-06-25): E0 + E1 (P0) + E2.S1 (P1) + E2.S2 (P2) landed
The §15 migration is executing. Plans: [../claudedocs/workflow_v1_to_v2_migration.md] (phases P0–P7) + [../claudedocs/spawn_v1_to_v2_migration.md] (epics E0–E5). **Done: E1 (P0 Tier-0) + E0 (live-path hardening), E2.S1 (P1 App + one-node Workflow skeleton), then E2.S2 (P2 research-loop port).**
- **E1 / P0 ✅** — pin `google-adk[a2a]>=2.3,<3` (lock=2.3.0, +`a2a-sdk` 0.3.26); `OLLAMA_API_BASE` env set in `build_model()`; `MCPToolset.close()` per drive call in `pipeline.py` (leak fix). *Note:* "toolset once-per-run" was infeasible in v1's `asyncio.run`-per-handler bridge → **delivered in E2.S2.T4 (node path).**
- **E0 ✅** — crawl4ai stdout→stderr (fd-level guard `dup2(2,1)` in `mcp/Crawl4AI_MCP/main.py`); agent JSON robustness (`jsonio.invoke_json_with_retry` + degrade-to-empty). E0.S3 (real-HW probe) ⏳ pending user.
- **E2.S1 / P1 ✅ (2026-06-24)** — v2 shell stood up on the `clarify → plan → CP1` slice. New `app/workflow.py` (`research` `@node(rerun_on_resume=True)` driving `ctx.run_node(clarifier)`→`ctx.run_node(planner)`→CP1; `build_research_workflow(*, clarifier_node, planner_node, store)` closure DI seam mirroring v1 `PipelineDeps`), `app/adk_app.py` (`app = App(name="localgpt_research", root_agent=Workflow(name="research"), resumability_config=ResumabilityConfig(is_resumable=True))`), `tests/test_workflow.py`. **CP1 is already a real `RequestInput(response_schema=ResearchPlan)`** (T4 done here, ahead of P3). **T6** `SessionStore` write-through additive (demoted E3.S2). **HARD GATE T5 PASSED** — kill mid-CP1 + resume-by-`invocation_id` re-runs CP1 only; clarify/plan auto-checkpoint-skipped (proven by body-side call counters staying at 1).
  - **Plan correction (verified vs installed ADK 2.3.0 + adk.dev docs):** `RequestInput` is `from google.adk.events` (not `google.adk.workflow`). **Two ADK behaviors:** START-entry param must be named `node_input`; `ctx.run_node` returns a `model_dump()` **dict** → coerced back via the agents' `parse_clarify_result`/`parse_plan` (latter re-applies the depth policy).
- **E2.S2 / P2 ✅ (2026-06-25)** — research loop ported into the proven shell (subagent-driven; `cavecrew-reviewer` between tasks + final integration review; ADK anchors re-verified vs 2.3.0 + `adk-docs` MCP).
  - **T1 (`8547088`)** — NEW `app/research_policy.py`: pure loop policy (`ResearchPhase`/`first_phase`/`next_phase`/`depth_budget`/`stop_rule`) **+** relocated pure `merge_ledger` (dedup-by-id, base wins, non-mutating); imports only `config`+`schemas`. `stage_machine.py` re-exports for v1; `pipeline._merge_ledger` delegates → **one** dedup/stop-rule impl shared by v1+v2 (makes parity meaningful).
  - **T2+T3 (`8f0ace0`)** — loop in `research` node after CP1: `for subtopic in approved.subtopics:` `while not stop_rule(ledger, subtopic, iteration=, new_claims=, budget=depth_budget(approved.depth)):` → `parse_scored_urls(ctx.run_node(acquirer, …))` → [CP3 seam] → `ctx.run_node(extractor, urls)` → `enrich_ledger(parse_ledger(ctx.run_node(verifier, subtopic.question)))` → `merge_ledger`; single accumulating `ClaimLedger`. Deterministic schedule order, **no custom `run_id`**. DI seam extended with `acquirer_node`/`extractor_node`/`verifier_node`. **Parity fix:** node path re-applies `enrich_ledger` (verifier node skips v1's auto-enrich).
  - **T4+T5 (`17d7be4`)** — **T4:** each owned `MCPToolset` built once at loop entry, shared across all passes, `close()`d in `finally`; injected = caller-owned (left open) → delivers the E1.T3-deferred lifecycle (`MCPToolset.close()` verified async+idempotent). **T5:** `cp3_hook` DI seam gated on `approved.depth == Depth.DEEP` (mirrors v1 `next_phase` MID_ACQUIRE); shallow/normal skip; no `RequestInput` yet → E3.S1.T2.
  - **T6 (`0dd0bfe`) — HARD GATE PASSED:** `tests/test_parity_v1_v2.py` drives v1 (`Orchestrator._run_research`) vs v2 (`workflow` loop) on identical canned inputs (one source-of-truth JSON set); terminal `ClaimLedger` structurally equal (claims by id + per-subtopic `kept_count`) over ≥1 shallow + 2 deep + all 3 stop-rule exits. Genuinely independent drivers, share only the pure policy. + resume-mid-loop test (each scheduled pass runs exactly once across the resume boundary).
- **Suite: 232 orchestrator (+12 E2.S2) + 43 MCP green, ruff clean. COMMITTED** to `dev` — E2.S2 `8547088`/`8f0ace0`/`17d7be4`/`0dd0bfe`; E2.S1 `21330ef`/`306f8d7`/`95cc51b`/`850b75b`. Not pushed.
- **Next = E3.S1 / P3** (real `RequestInput` CP2/CP3 + CP3 free-text input adapter `+add/-exclude/r/d`; retire console `checkpoint.py` in lockstep). Both HARD GATES so far green (E2.S1 resume, E2.S2 parity). E4–E5 still pending.

**Design baseline (v2, ratified 2026-06-23):** **ADK 2.x**, **dynamic-workflow** orchestration (`@node`/`Workflow`/`ctx.run_node`), **ADK-owned sessions/resume** (`ResumabilityConfig`; `data/sessions/*.json` → write-through export), `RequestInput` HITL, **single local-first A2A boundary** (`to_a2a` / `adk api_server --a2a`, localhost) with the 6 specialists kept as local nodes (not A2A peers). Master spec: [architecture.md] (§0/§13/§14/§15). User decisions: ADK 2.x ✓, ADK-owned sessions ✓, A2A local-first ✓, deploy = `adk api_server` ✓.

## Prior Phase (v1 build — complete)
**Story 5 (Integration & Research Loop) — DONE + offline-tested; live path validated to the agent-output boundary.** Built 2026-06-23: T4.1 per-subtopic research loop + stop-rule, T4.2 composition root (`app/pipeline.py`) wiring all six real agents + CP1/CP2/CP3, T4.3 adaptive depth→iteration budgets. orchestrator **203** tests green (was 181: +stop-rule/research-loop/pipeline/jsonio), MCP **43** green, ruff clean. Earlier: Story 2/3/4 done.

**Live smoke (user bar = live run):** reached RESEARCH and exercised the real chain — Ollama Clarifier→Planner, MCP subprocess launch, `discover_urls` (28 real URLs), triage scoring, fenced-JSON parsing. Did NOT reach a final report; blocked by two issues *outside* Story-5 wiring (see Known Issues). Composition-root integration itself proven correct.

## Current Focus
Pipeline now has: shared one-hot model factory (`app/llm.py`, ADK `LiteLlm`→Ollama), deterministic stage machine + orchestrator skeleton (INTAKE→DONE, deep-only CP3 via `ResearchPhase.MID_ACQUIRE`), all six agents (Clarifier/Planner/Acquirer/Extractor/Verifier/Writer), Semantic Scholar citation client (`app/citation.py`), Story 2 MCP extensions (eTLD+1, dedup, seed ingest, PDF routing, rate-limit backoff), and Story 4 checkpoints (`app/checkpoint.py`: `cp1/cp2/cp3_checkpoint` + `Handler` wrappers; `post_handlers`/`phase_handlers` hooks in the orchestrator; `SessionStore.save/load_scored_urls` for CP3's edited source list). All tests run offline (mocked/stubbed model, monkeypatched stdin/editor). **Story 4 nuance**: the checkpoint *mechanism* + handlers landed and are G5-verified by tests that register them directly; registration into the live composition root happens with Story 5 integration (alongside real agent wiring — skeleton still uses stub handlers).

## Recent Decisions (from brainstorm)
- Verification = cross-corroborate-or-flag (no per-claim confidence scoring required).
- Autonomy = checkpoint approvals (plan + draft gates).
- Depth = adaptive (query-driven).
- Framework = open to recommendation (NOT locked to Google ADK).
- Clarification triggers = ambiguous scope + missing constraints, before run start only.
- Sources = mixed; current MCP aggregates SerpAPI + DuckDuckGo + arXiv, to be improved.
- Output = structured report (exec summary / sections / sources / contradictions appendix).

## Design Decisions (this phase)
- **Framework**: Google ADK + A2A (kept — MCP already ADK-integrated; RAM-neutral).
- **Topology**: hierarchical coordinator — Orchestrator delegates to Clarifier/Planner/Acquirer/Extractor/Verifier/Writer.
- **Model**: one hot Qwen2.5-14B Q4 (8B fallback) role-prompted + resident nomic-embed-text. ~12.5 GB VRAM. Sequential stages (16 GB RAM bottleneck).
- **Tool access**: only Acquirer/Extractor hold MCP write tools (least privilege).
- Open questions Q1–Q6 all resolved in [architecture.md] (model fit, independent-source = eTLD+1 + cosine<0.92, MCP 5 extensions, resume via stage.json, stop-rule, CLI checkpoints).

## Story 5 — DONE (2026-06-23)
- **T4.1 Research loop** — `Orchestrator._run_research` (per-subtopic, budget-bounded) + `stage_machine.stop_rule`/`depth_budget`; ledger accumulates across passes; phase handlers ACQUIRE/EXTRACT/VERIFY.
- **T4.2 Composition root** — `app/pipeline.py` `build_orchestrator()`/`run_pipeline()`: wires six agents, CP1→`post_handlers[PLAN]`, CP2→`post_handlers[WRITE]`, CP3→`phase_handlers[MID_ACQUIRE]`; sync↔async bridge (`_run_sync`); `CheckpointRejected` abort + report publish. `main.py` rewired.
- **T4.3 Adaptive depth** — `config.MAX_ITER_{SHALLOW,NORMAL,DEEP}` → `depth_budget(depth)` consumed by stop-rule. target_evidence/CP3/citation-BFS already depth-gated.
- **Dep fix**: added `mcp` to `orchestrator/pyproject.toml` (ADK `MCPToolset` requires the `mcp` SDK; was undeclared, masked by lazy import + offline fakes).
- **Robustness**: `app/jsonio.py loads_first_json` (tolerates code fences + trailing prose) wired into `acquirer.parse_scored_urls` + `verifier.parse_ledger`.
- **Verifier**: added `_build_default_toolset()` (filter `search_chunks`) for live chunk retrieval.

## Known Issues / Follow-ups (live path — NOT Story-5 wiring)
1. ✅ **RESOLVED (E0.S1, 2026-06-23)** — crawl4ai MCP stdout pollution (`[INIT]... Crawl4AI` banner corrupting stdio JSON-RPC). Fixed by an fd-level guard in `mcp/Crawl4AI_MCP/main.py`: `dup2(2,1)` repoints fd-1 to stderr + `sys.stdout` rebound over the saved real-stdout fd so MCP keeps a clean JSON-RPC channel. MCP 43 green.
2. ✅ **RESOLVED (E0.S2, 2026-06-23)** — agent JSON reliability under tool timeouts (Acquirer/Verifier returning non-JSON prose → `loads_first_json` raises). Fixed by `jsonio.invoke_json_with_retry` (one JSON-only re-ask) + graceful degrade (`acquire→[]`, `verify→empty ledger`); genuine Pydantic `ValidationError` still surfaces.
3. ⏳ **PENDING USER (E0.S3)** — run setup probe on real hardware: `cd orchestrator && python scripts/check_setup.py` — pull `qwen2.5:14b-instruct-q4_K_M` + embed; confirm VRAM < 14 GB (Gate G2).

## Active Considerations
- 16 GB RAM binding constraint, not VRAM → sequential execution structural.
- crawl4ai MCP exists (`mcp/Crawl4AI_MCP/`) — extend (6 additions now including Semantic Scholar client), don't rebuild.
- Verifier independence test depends on MCP extensions #1 (dedup) + #2 (eTLD+1) — build those first.
- Semantic Scholar citation BFS + temporal drift detection = zero new models, zero VRAM overhead.
- CP3 only fires on `depth=deep` — shallow/normal plans unaffected, no friction added.
- Knowledge graph + session comparison deferred to Story 7 (post-MVP).

## Design Decisions (2026-06-21 update — competitive research + brainstorm)
- **CP3 scope**: fires only for `depth=deep` plans. User approved.
- **Knowledge graph**: deferred to Story 7. Not in v1 scope.
- **Citation BFS depth**: 2 hops. User approved. Bounded by relevance gate + per-subtopic budget cap.
- **P2/P4 integration**: contradiction confidence scoring + temporal drift detection extend Verifier (T2.6/S3.6). No new agents.
- **T1.6**: Semantic Scholar citation graph API client lives in `orchestrator/app/citation.py` (httpx utility), not inside MCP server. Called directly by Acquirer agent.