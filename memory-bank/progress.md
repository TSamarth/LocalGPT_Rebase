# Progress

## Status: Stories 1–5 done; **v1→v2 migration: E1 (P0) + E0 + E2.S1 (P1) landed.** E1 (2026-06-23): pin `google-adk[a2a]>=2.3,<3` (lock 2.3.0 +`a2a-sdk` 0.3.26), `OLLAMA_API_BASE` env in `build_model()`, `MCPToolset.close()` per drive call. E0 (2026-06-23): crawl4ai stdout→stderr fd-guard (`mcp/Crawl4AI_MCP/main.py`), `jsonio.invoke_json_with_retry`+degrade. **E2.S1 (2026-06-24):** v2 shell on the clarify→plan→CP1 slice — `app/workflow.py` (`research` `@node` + `ctx.run_node` + `build_research_workflow` DI seam) + `app/adk_app.py` (`App`+`ResumabilityConfig`) + `tests/test_workflow.py`; CP1 is a real `RequestInput`; **HARD GATE T5 (resume-by-`invocation_id` re-runs CP1 only) PASSED**. **220 orchestrator (+4) + 43 MCP green, ruff clean. COMMITTED** to `dev` (`21330ef`/`306f8d7`/`95cc51b`/`850b75b`, unpushed). E0.S3 real-HW probe ⏳ user. Next = E2.S2/P2 (research-loop port). See [../claudedocs/spawn_v1_to_v2_migration.md] Execution Log.

## Story 5 (Integration & Research Loop) landed 2026-06-23 — — T4.1 per-subtopic loop + stop-rule, T4.2 composition root (`app/pipeline.py`) wiring 6 agents + CP1/CP2/CP3, T4.3 depth→iteration budgets. orchestrator **203** tests green (181→+22: stop-rule/research-loop/pipeline/jsonio), MCP 43 green, ruff clean. Added `mcp` dep; hardened JSON parsing (`app/jsonio.py`); verifier `search_chunks` toolset. Live smoke proved the chain through MCP discover/triage/parse but didn't finish — blocked by (1) crawl4ai MCP stdout banner corrupting JSON-RPC and (2) tool-timeout→non-JSON agent output (both outside Story-5 wiring; logged as follow-ups in activeContext.md). **Next: agent JSON-robustness + MCP stdout→stderr, then full live run.**

## What Works (exists today)
- crawl4ai MCP server at `mcp/Crawl4AI_MCP/`: full pipeline — discover_urls (SerpAPI+DDG+arXiv+SemanticScholar+GoogleSERP), score_and_triage_urls, crawl_url/many/deep/adaptive, search_chunks, get_crawl_stats. SQLite + ChromaDB + Ollama embeddings. ADK MCPToolset integration designed.
- **orchestrator/ (NEW)** — A2A pipeline package, Story 1 foundation:
  - `app/schemas.py` — Pydantic artifact contracts: ResearchPlan/Subtopic, ScoredURL, ClaimLedger/Claim/SourceRef, ClarifyResult, StageState + enums (Depth, SourceClass, CrawlStrategy, ClaimStatus, Stage). ClaimLedger has `for_subtopic`/`kept_count` helpers (drive stop-rule).
  - `app/config.py` — dataclass+`_env` config (models, MCP cwd, dirs, stop-rule budgets, independence threshold, depth→target_evidence).
  - `app/session.py` — `SessionStore`: create/resume, stage.json pointer, plan/ledger/draft persistence, final report write to `data/reports/{id}.md`.
  - `main.py` smoke entry, `scripts/check_setup.py` (G2 probe), `.env.example`, `pyproject.toml`, `.gitignore`.
  - **Tests: 19 passing** (test_schemas.py, test_session.py) via local .venv (pydantic+dotenv+pytest). main.py verified end-to-end (creates session).
- **T0.4 schema extension (Gate G1) done** — new optional, backward-compatible fields on the frozen contracts:
  - `ScoredURL`: `publication_date: date|None`, `citation_refs: list[str]` (citation BFS / date enrichment, §3.4/§11).
  - `SourceRef`: `publication_date: date|None` (chunk-metadata propagation for temporal drift).
  - `Claim`: `temporal_status: TemporalStatus|None` (current/dated/uncertain).
  - New `Contradiction` model (replaces bare `SourceRef` in `Claim.contradictions`): `+confidence_score: float [0..1]`, `+conflict_type: ConflictType` (factual/methodological/temporal_drift). Legacy 3-field JSON still validates via defaults.
  - New enums `ConflictType`, `TemporalStatus`. Config: `TEMPORAL_DRIFT_THRESHOLD_MONTHS=18`.
  - +5 tests (defaults absent, date roundtrip, contradiction scoring roundtrip, confidence bounds, legacy-JSON load).
- **Wave 0 — shared foundation (serial, committed before parallel branching)**:
  - `orchestrator/app/llm.py` (NEW) — one-hot model factory. `ollama_model_str(name)` → `ollama_chat/{name}`; `build_model()` → ADK `LiteLlm` bound to Ollama (api_base, num_ctx, keep_alive, drop_params); `build_agent(name, role_prompt, output_schema|tools, ...)` → `LlmAgent` (raises if output_schema AND tools both set — ADK mutual exclusion). This was the ADK→Ollama A2A spike, de-risked here.
  - `orchestrator/app/stage_machine.py` (NEW) — `STAGE_ORDER`, `next_stage`, `is_terminal`; `ResearchPhase` enum (ACQUIRE/MID_ACQUIRE/EXTRACT/VERIFY), `first_phase`, `next_phase(phase, depth)` (MID_ACQUIRE/CP3 fires deep-only). MID_ACQUIRE lives here, NOT in the frozen `Stage` enum.
  - `orchestrator/app/orchestrator.py` (NEW) — `Orchestrator` deterministic driver: `start/resume/step/run_to_completion`, `_run_research_pass` (records `phase_trace`), stub handlers per stage so skeleton runs INTAKE→DONE before agents land.
  - `orchestrator/app/agents/__init__.py` (NEW package marker). `config.py` (both packages) extended with all keys agents/tracks need (S2 citation BFS keys orchestrator-side; per-domain rate-limit/backoff + dedup + seed keys MCP-side). `main.py` wired to Orchestrator. `pyproject.toml` += `litellm>=1.0`, pytest `pythonpath=["."]`. `test_stage_machine.py` (9 tests).
- **Wave A — Story 2 MCP extensions (`mcp/Crawl4AI_MCP/`)**:
  - T1.1 eTLD+1: `app/domain.py` (tldextract, offline) + SQLite `chunks.etld1` column.
  - T1.2 dedup: `app/tools/dedup.py` (cosine ≥ 0.92 collapses mirrors) + `crawled_pages.duplicate_of` column.
  - T1.3 seed ingest: `app/tools/seed.py` (`ingest_seeds`), registered in `common.py`.
  - T1.4 PDF/arXiv routing + T1.5 per-domain rate-limit/backoff: `app/ratelimit.py` (`DomainRateLimiter`, `crawl_with_retry`) + edits to `crawl.py`/`adaptive_crawl.py`/`discover.py`/storage.
  - `tests/test_story2.py` (15 tests). **MCP suite: 43 tests green.**
- **Wave A — Story 3 agents (`orchestrator/app/`)**:
  - T2.6 citation: `citation.py` — Semantic Scholar BFS. `extract_paper_id` (arXiv/DOI/S2), `CitationClient.get_paper_metadata/bfs`, module wrappers; injectable http_client + sleep. (35 tests.)
  - T3.2 Clarifier: `agents/clarifier.py` — `build_clarifier`, `parse_clarify_result`, `async clarify`. (20 tests.)
  - T3.3 Planner: `agents/planner.py` — `build_planner`, `apply_depth_targets` (depth→2/3/5 via TARGET_EVIDENCE_*), `async plan`, `parse_plan`. (13 tests.)
  - T3.5 Extractor: `agents/extractor.py` — `build_extractor(toolset)`, `chunk_metadata_for` (publication_date passthrough, no inference), `STRATEGY_TOOL_NAMES`; lazy-imports `MCPToolset`. (9 tests.)
  - T3.7 Writer: `agents/writer.py` — `build_writer`, `coverage_report`, `split_contradictions` (temporal-drift separated from factual), `render_report`. (9 tests.)
  - **orchestrator suite: 114 tests green.**
- **Wave B — Story 3 long-pole agents (`orchestrator/app/agents/`, 2026-06-22, 2 parallel subagents, disjoint new files)**:
  - T3.4 Acquirer: `agents/acquirer.py` — MCP tool agent (`build_acquirer`, `tool_filter=[discover_urls, score_and_triage_urls]`, `output_schema=None` like Extractor). `should_run_citation_bfs` (academic AND deep only), `enrich_with_citations` (drives `citation.py` `CitationClient.bfs`, sets `publication_date`+`citation_refs`, dedups), `parse_scored_urls`, injectable async `acquire`. (19 tests.)
  - T3.6 Verifier (trust core): `agents/verifier.py` — deterministic policy (pure, model-free): `are_independent`/`count_independent_sources` (diff etld1 AND cosine < 0.92), `classify_claim_status` (kept ≥2 independent / flagged / uncorroborated), `score_contradiction` (3-tier additive rubric 0–0.33 each → [0,1]), `classify_conflict` (≥18mo gap → TEMPORAL_DRIFT + dated/current, missing date → UNCERTAIN), `enrich_ledger`, `parse_ledger`, injectable async `verify`. `build_verifier` reasoning-only (`output_schema=ClaimLedger`) or with injected `search_chunks` toolset (schema dropped). (32 tests.)
  - **orchestrator suite: 165 tests green** (114 + 19 + 32). ruff clean. Schemas/config/llm/citation untouched (all keys pre-existed).
- **Story 4 — human-in-loop checkpoints (`orchestrator/app/`, 2026-06-22, inline build)**:
  - `app/checkpoint.py` (NEW) — pure console flows: `cp1_checkpoint(plan)→ResearchPlan` and `cp2_checkpoint(draft)→str` (approve/edit/reject; edit round-trips through `$EDITOR`/`notepad` temp file + Pydantic re-validate; reject raises `CheckpointRejected`), `cp3_checkpoint(urls)→(filtered, needs_supplemental)` (deep-only source-list inspect: `+add`/`-exclude <n>`/`r redirect`/`d done`; add/redirect set `needs_supplemental`). `Handler` wrappers `cp1/cp2/cp3_handler(orch)` do the `store` I/O. `_launch_editor` + `input` are the only side effects (monkeypatched in tests).
  - `app/orchestrator.py` — `post_handlers: dict[Stage,Handler]` (fire after a stage; CP1→PLAN, CP2→WRITE) called in `step()`; `phase_handlers: dict[ResearchPhase,Handler]` (fire after a sub-phase; CP3→MID_ACQUIRE) called in `_run_research_pass()`. Both default to `_stub`. CP3 `needs_supplemental` appends `ResearchPhase.ACQUIRE` to `phase_trace` (re-entry hook).
  - `app/session.py` — `SessionStore.save/load_scored_urls` (`scored_urls.json`) so CP3 can persist its edited candidate list (gap-fill; Acquirer's `OUTPUT_KEY="scored_urls"`).
  - Tests: `test_checkpoint.py` (11 — CP1/CP2/CP3 flows incl. editor round-trip, invalid-edit fallback, reject) + `test_orchestrator.py` (5 — post/phase handler wiring, CP3 skip on shallow/normal, fire on deep, supplemental re-entry). **orchestrator suite: 181 tests green** (165 + 16). ruff clean.
  - **Carry-forward**: checkpoint handlers are NOT yet registered in a live composition root (skeleton uses stub handlers); G5 verified by tests that register them directly. Live registration lands with Story 5 integration (T4.2) alongside real agent wiring.
- **Build mechanics**: 6 parallel git-worktree subagents off the Wave 0 commit, disjoint new-file ownership (shared surfaces pre-staged in Wave 0, read-only after), octopus-merged to `dev`. ruff clean. Commit author/committer emails rewritten yahoo → GitHub noreply (push protection); pushed to origin/dev.

## What's Defined (planning)
- Requirements spec ([requirements.md]), architecture ([architecture.md]), workflow ([../claudedocs/workflow_a2a_research.md]), task hierarchy ([../claudedocs/spawn_a2a_research.md]).

## What's Left To Build
- [x] A2A framework selection (ADK+A2A) — design
- [x] Model strategy (one hot Qwen2.5-14B Q4) — design
- [x] **Artifact schemas (Story 1 / T0.1)** — Pydantic v2, 14 tests green
- [x] **Config + scaffold (Story 1 / T0.2)**
- [x] **Session persistence / resume (Story 1 / T0.3)**
- [x] **T0.4 schema extension (Gate G1)**: `publication_date`+`citation_refs` on `ScoredURL`; `publication_date` on `SourceRef`; `temporal_status` on `Claim`; new `Contradiction` model w/ `confidence_score`+`conflict_type`. All optional, backward compatible. 19 tests green.
- [ ] Setup probe RUN on real hardware (T1.3 / G2) — script ready, user must run + pull models
- [x] **Story 2** — crawl4ai MCP extensions (Wave A):
  - [x] T1.1 eTLD+1 field (critical path) — `app/domain.py` + `chunks.etld1`
  - [x] T1.2 Content dedup (critical path, blocks Verifier) — `tools/dedup.py` + `crawled_pages.duplicate_of`
  - [x] T1.3 Seed-URL ingest — `tools/seed.py`
  - [x] T1.4 PDF quality — arXiv/PDF routing in `crawl.py`/`adaptive_crawl.py`
  - [x] T1.5 Rate-limit backoff — `app/ratelimit.py`
  - [x] T1.6 Semantic Scholar citation graph API client (`orchestrator/app/citation.py`)
- [x] **Story 3** — Agent layer (Wave 0 + Wave A + Wave B done; all six specialists green):
  - [x] Orchestrator + stage machine (MID_ACQUIRE phase for CP3) — `orchestrator.py` + `stage_machine.py` (T3.1)
  - [x] Shared one-hot model factory — `app/llm.py` (Wave 0, ADK→Ollama spike)
  - [x] Clarifier agent (T3.2)
  - [x] Planner agent (T3.3)
  - [x] **Acquirer agent (T3.4)** — `agents/acquirer.py` (+citation BFS, 2-hop, relevance-gated, deep+academic only). Wave B.
  - [x] Extractor agent (T3.5) (+publication_date propagation to chunk metadata)
  - [x] **Verifier agent (T3.6)** — `agents/verifier.py` (+independence test eTLD+1+cosine, +contradiction confidence scoring 3-tier, +temporal drift detection, +ClaimLedger assembly). Wave B.
  - [x] Writer agent (T3.7) (+temporal drift sub-section in contradictions appendix)
- [x] **Story 4** — Checkpoint gates (Gate G5 cleared, 181 tests):
  - [x] Checkpoint CLI (approve/edit/reject, $EDITOR) — `app/checkpoint.py`
  - [x] CP1 + CP2 hooks (`post_handlers` on PLAN/WRITE) — `orchestrator.py`
  - [x] CP3 mid-acquisition checkpoint (deep plans only, `phase_handlers` on MID_ACQUIRE) + `SessionStore.save/load_scored_urls`
  - [ ] Live registration of checkpoint handlers into composition root — deferred to Story 5 (T4.2)
- [x] **Story 5** — Research loop + integration + adaptive depth (incl. live checkpoint registration) — DONE 2026-06-23 (203 orchestrator tests green)
- [ ] **Story 5.5 — v2 ADK 2.x re-alignment (DESIGN ratified 2026-06-23; impl in progress)** — migrate v1 hand-rolled orchestration → ADK 2.x per [architecture.md] §15. Plans: [../claudedocs/workflow_v1_to_v2_migration.md] (P0–P7) + [../claudedocs/spawn_v1_to_v2_migration.md] (E0–E5):
  - [x] **Tier 0 correctness (E1/P0, 2026-06-23)**: `OLLAMA_API_BASE` env in `build_model()`; pinned `google-adk[a2a]>=2.3,<3` (lock 2.3.0); `MCPToolset.close()` per drive call. *(once-per-run toolset deferred to node path / E2.S2.T4 — infeasible in v1 sync-bridge.)*
  - [x] **Live-path hardening (E0, 2026-06-23, parallel side-track)**: crawl4ai stdout→stderr fd-guard; agent JSON retry+degrade. *(Not in §15; cleared the two Story-5 live blockers so P7 E2E can pass.)*
  - [x] **`App` + one-node `Workflow` (Clarifier→Planner→CP1) with `ResumabilityConfig`; ADK-owned resume validated (E2.S1/P1, 2026-06-24)** — `app/workflow.py` (`research` `@node(rerun_on_resume=True)` + `ctx.run_node` clarifier/planner + `build_research_workflow` closure DI seam) + `app/adk_app.py` (`App(name="localgpt_research")` + `ResumabilityConfig(is_resumable=True)`). **CP1 already a real `RequestInput(response_schema=ResearchPlan)`** (T4); `SessionStore` write-through kept as additive safety net (T6). **HARD GATE T5 ✅**: kill mid-CP1 + resume-by-`invocation_id` re-runs CP1 only (clarify/plan auto-checkpoint-skipped). 4 tests in `tests/test_workflow.py` (a/b/c+T6); 220 green; committed `21330ef`/`306f8d7`/`95cc51b`/`850b75b`. *Corrections vs plan:* `RequestInput` from `google.adk.events`; START param must be `node_input`; `ctx.run_node` returns a `model_dump()` dict.
  - [ ] Port research loop (`stop_rule`) + Acquirer/Extractor/Verifier as `ctx.run_node` nodes — **E2.S2/P2, next**
  - [ ] Replace CP2/CP3 with `RequestInput` (+ CP3 input adapter); retire console `checkpoint.py` *(CP1 already done in E2.S1.T4)*
  - [ ] Flip session ownership to ADK; demote `SessionStore` → export callback/plugin; retire `stage.json` resume
  - [ ] Expose pipeline as local-first A2A (`to_a2a`/`adk api_server --a2a`) + agent card; wire CLI to `/run_sse`
  - [ ] Retire `orchestrator.py` + `stage_machine.py` driver + `pipeline.py` `_run_sync`
- [ ] **Story 6** — E2E acceptance (AC1–AC7) + OOM validation + resume-by-`invocation_id` + localhost A2A round-trip
- [ ] **Story 7** (post-MVP) — Knowledge graph extraction + session comparison

## Known Issues / Risks
- 16 GB RAM bottleneck → risk of OOM if stages not strictly sequenced.
- "Independent source" hard to define (syndication/mirrors). Resolved approach: eTLD+1 + cosine<0.92; threshold needs tuning (Story 2 / T1.2).
- Coverage = "all aspects" → stop-rule defined (target_evidence + diminishing returns + iteration cap).
- google-adk + litellm now installed (`uv sync`); ADK `LiteLlm`→Ollama spike validated in `app/llm.py`. All agent tests run offline with mocked/stubbed model — no live Ollama needed for the suite.
- **Carry-forward (Wave B / integration)**: the `mcp` ADK extra is NOT in `orchestrator/pyproject.toml`. Extractor (T3.5) lazy-imports `MCPToolset` so offline tests pass, but the live MCP/Extractor path needs that extra added before E2E.

## Decision Log
- 2026-06-24: **E2.S1 / P1 landed — v2 shell on the clarify→plan→CP1 slice (subagent-driven, 3 sequential subagents + review between).** New `app/workflow.py` + `app/adk_app.py` + `tests/test_workflow.py`; v1 driver untouched (additive). DI seam = **closure** (`build_research_workflow(*, clarifier_node, planner_node, store)`) mirroring v1 `PipelineDeps` — tests inject canned `@node` stubs, prod defaults to the existing `build_clarifier`/`build_planner` `LlmAgent`s (plug into `ctx.run_node` unchanged). CP1 implemented as a real `RequestInput(response_schema=ResearchPlan)` (T4, ahead of P3); `SessionStore` write-through kept additive (T6, demoted E3.S2). **HARD GATE E2.S1.T5 PASSED** — resume-by-`invocation_id` re-runs CP1 only, clarify/plan skipped (proven by body-side call counters: 1 after pause, still 1 after resume). **Plan API corrections (verified vs installed ADK 2.3.0 + adk.dev MCP docs):** (1) `RequestInput` is `from google.adk.events`, not `google.adk.workflow`; (2) the START-entry node's passthrough param must be named `node_input`; (3) `ctx.run_node` returns a node's `BaseModel` as a `model_dump()` dict → coerced via `parse_clarify_result`/`parse_plan`. Names: `Workflow(name="research")`, `App(name="localgpt_research")`. 216→**220 orchestrator** (+4), MCP 43 green, ruff clean. **Committed** `21330ef`/`306f8d7`/`95cc51b`/`850b75b` to `dev` (unpushed). Next = E2.S2/P2 loop port (unblocked by the green resume gate).
- 2026-06-23: **Migration kickoff — E1 (P0) + E0 executed in parallel.** Ran the spawn-doc recommended first slice: E1 (Tier-0 correctness, main thread) + E0 (live-path hardening, background agent) on one working tree, disjoint files. **E1.T3 scope correction:** the §15/spawn "MCPToolset once across N passes" is infeasible in v1 — `pipeline._run_sync` runs each handler under its own `asyncio.run`, so a stdio toolset (bound to one loop+subprocess) can't span passes; shipped the achievable fix (close-per-drive-call, kills the leak) and moved the single-long-lived-toolset pattern to E2.S2.T4 (node path, one loop). E1.T2 touched only `llm.py` (config.py unneeded). a2a-sdk 0.3.26 pulled by the `[a2a]` extra. 203→**216 orchestrator** tests (+5 E1, +13 E0), MCP 43 green, ruff clean. Uncommitted. Next = E2.S1/P1.
- 2026-06-23: **v2 design pivot ratified.** ADK-docs alignment review ([../claudedocs/adk_alignment_review.md]) found the repo runs ADK 2.3.0 but codes like 1.x. Committed to: **ADK 2.x** (pin `>=2.3,<3`); **dynamic-workflow** orchestration; **ADK-owned sessions/resume** (`data/sessions/*.json` → export); **`RequestInput` HITL**; **single local-first A2A boundary** (`to_a2a`/`adk api_server --a2a`, localhost) with the 6 specialists as local nodes (ADK guidance: don't A2A in-process shared-model agents). Deploy = `adk api_server`. [architecture.md] rewritten to v2 (§0/§13/§14/§15); CONTEXT/systemPatterns/techContext synced. Open Q answers: ADK 2.x ✓, ADK sessions ✓, A2A pipeline-edge local-first ✓, api_server ✓. Migration = §15 (next: `/sc:workflow`).
- 2026-06-18: Brainstorm complete. Framework left open. Verification = corroborate-or-flag. Checkpoints at plan + draft. Adaptive depth.
- 2026-06-19: Design — ADK+A2A, hierarchical coordinator, one hot 14B model. Workflow + task hierarchy produced.
- 2026-06-19: Story 1 implemented. Schemas use Pydantic v2 (ADK-native). New package at `orchestrator/` (sibling to `mcp/`). 14 tests green.
- 2026-06-21: T0.4 schema extension (Gate G1) done — added optional date/citation/temporal/contradiction-scoring fields + `Contradiction` model + 2 enums + config threshold. Backward compatible; 19 tests green. Unblocks Story 2.
- 2026-06-21: Competitive research (vs. Firecrawl) + brainstorm. Added P1 citation BFS (2-hop, relevance-gated, deep plans only), P2 contradiction confidence scoring, P4 temporal drift detection, P5 CP3 (deep only). Deferred P3 knowledge graph + P6 session comparison to Story 7. Schema extension needed pre-Story-2. T1.6 Semantic Scholar API client added to Story 2.
- 2026-06-22: Parallel build of Story 2 + Story 3 via git worktrees. **Wave 0** (serial): shared `llm.py` factory (ADK→Ollama spike), `stage_machine.py`, `orchestrator.py` skeleton, config keys in both packages, deps. **Wave A** (6 parallel worktree subagents, disjoint new files, octopus-merged): Story 2 MCP extensions (T1.1–T1.6) + agents T3.2/T3.3/T3.5/T3.7. orchestrator 114 + MCP 43 tests green, ruff clean. Conflict-free via pre-staging all shared mutable surfaces in Wave 0 (read-only after). **Wave B deferred**: Acquirer (T3.4) + Verifier (T3.6) — deps now merged, both unblocked.
- 2026-06-22: **Wave B** — Acquirer (T3.4) + Verifier (T3.6) built by 2 parallel python-expert subagents, disjoint new files (`acquirer.py`/`test_acquirer.py`, `verifier.py`/`test_verifier.py`), no shared surface touched (schemas/config/llm/citation frozen, all keys pre-existed). Verifier policy kept pure/deterministic (independence, 3-tier confidence, temporal drift) — LLM judges content only. orchestrator 165 tests green, ruff clean. **Story 3 complete; G4 cleared for all six specialists.** Next: Story 4 checkpoints + Story 5 research loop (wire Acquire→Extract→Verify).
- 2026-06-22: Rewrote 9 unpushed commit emails yahoo → GitHub noreply (`47295533+TSamarth@users.noreply.github.com`) to clear push protection; local `user.email` set to noreply; pushed origin/dev (fast-forward).
- 2026-06-22: **Story 4** — human-in-loop checkpoints (CP1/CP2/CP3) built inline (not worktrees — 4 non-conflicting lines in `orchestrator.py`). New `app/checkpoint.py` (pure console flows + `Handler` wrappers); orchestrator gains `post_handlers` (CP1/CP2 after PLAN/WRITE in `step()`) and `phase_handlers` (CP3 after MID_ACQUIRE in `_run_research_pass()`), both `_stub`-defaulted. **Two plan deviations**: (1) added `SessionStore.save/load_scored_urls` — plan assumed it existed but it didn't; (2) put wiring tests in NEW `test_orchestrator.py` — plan said modify it but orchestrator tests actually lived in `test_stage_machine.py` (left untouched). **Gate G5 cleared**: `test_cp1/cp2_blocks_until_approve`, `test_cp3_skips_on_shallow_plan` (+ fires-on-deep). 181 tests green, ruff clean, pushed origin/dev (2 commits: docs migration + Story 4). Checkpoint handlers register into the live composition root at Story 5 (T4.2); skeleton still stubs.
- 2026-06-22: Docs migration — added `CONTEXT.md` (project context), slimmed `CLAUDE.md` to behavioral guidelines, removed superseded `claudedocs/parallel_impl_story2_story3.md`, updated `stage_machine.py` docstring refs CLAUDE.md→CONTEXT.md. Note: `claudedocs/story4_checkpoints.md` (Story 4 plan input) was untracked and is no longer on disk — never committed, nothing lost from git.