# Progress

## Status: Story 2 (MCP extensions) + Story 3 agents (3.1/3.2/3.3/3.5/3.7) done via parallel worktree build. orchestrator 114 tests green, MCP 43 tests green. Wave B (Acquirer 3.4 + Verifier 3.6) deferred.

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
- [ ] **Story 3** — Agent layer (Wave 0 + Wave A done; Wave B deferred):
  - [x] Orchestrator + stage machine (MID_ACQUIRE phase for CP3) — `orchestrator.py` + `stage_machine.py` (T3.1)
  - [x] Shared one-hot model factory — `app/llm.py` (Wave 0, ADK→Ollama spike)
  - [x] Clarifier agent (T3.2)
  - [x] Planner agent (T3.3)
  - [ ] **Acquirer agent (T3.4) — DEFERRED to Wave B** (+citation BFS, 2-hop, relevance-gated, deep plans only). Deps 2.1+2.3+2.6 now merged → unblocked.
  - [x] Extractor agent (T3.5) (+publication_date propagation to chunk metadata)
  - [ ] **Verifier agent (T3.6) — DEFERRED to Wave B** (+independence test eTLD+1+cosine, +contradiction confidence scoring, +temporal drift detection, +ClaimLedger assembly). Deps 2.1+2.2 now merged → unblocked. Long pole.
  - [x] Writer agent (T3.7) (+temporal drift sub-section in contradictions appendix)
- [ ] **Story 4** — Checkpoint gates:
  - [ ] Checkpoint CLI (approve/edit/reject, $EDITOR)
  - [ ] Wire CP1 + CP2 into stage machine
  - [ ] CP3 mid-acquisition checkpoint (deep plans only)
- [ ] **Story 5** — Research loop + integration + adaptive depth
- [ ] **Story 6** — E2E acceptance (AC1–AC7) + OOM validation
- [ ] **Story 7** (post-MVP) — Knowledge graph extraction + session comparison

## Known Issues / Risks
- 16 GB RAM bottleneck → risk of OOM if stages not strictly sequenced.
- "Independent source" hard to define (syndication/mirrors). Resolved approach: eTLD+1 + cosine<0.92; threshold needs tuning (Story 2 / T1.2).
- Coverage = "all aspects" → stop-rule defined (target_evidence + diminishing returns + iteration cap).
- google-adk + litellm now installed (`uv sync`); ADK `LiteLlm`→Ollama spike validated in `app/llm.py`. All agent tests run offline with mocked/stubbed model — no live Ollama needed for the suite.
- **Carry-forward (Wave B / integration)**: the `mcp` ADK extra is NOT in `orchestrator/pyproject.toml`. Extractor (T3.5) lazy-imports `MCPToolset` so offline tests pass, but the live MCP/Extractor path needs that extra added before E2E.

## Decision Log
- 2026-06-18: Brainstorm complete. Framework left open. Verification = corroborate-or-flag. Checkpoints at plan + draft. Adaptive depth.
- 2026-06-19: Design — ADK+A2A, hierarchical coordinator, one hot 14B model. Workflow + task hierarchy produced.
- 2026-06-19: Story 1 implemented. Schemas use Pydantic v2 (ADK-native). New package at `orchestrator/` (sibling to `mcp/`). 14 tests green.
- 2026-06-21: T0.4 schema extension (Gate G1) done — added optional date/citation/temporal/contradiction-scoring fields + `Contradiction` model + 2 enums + config threshold. Backward compatible; 19 tests green. Unblocks Story 2.
- 2026-06-21: Competitive research (vs. Firecrawl) + brainstorm. Added P1 citation BFS (2-hop, relevance-gated, deep plans only), P2 contradiction confidence scoring, P4 temporal drift detection, P5 CP3 (deep only). Deferred P3 knowledge graph + P6 session comparison to Story 7. Schema extension needed pre-Story-2. T1.6 Semantic Scholar API client added to Story 2.
- 2026-06-22: Parallel build of Story 2 + Story 3 via git worktrees. **Wave 0** (serial): shared `llm.py` factory (ADK→Ollama spike), `stage_machine.py`, `orchestrator.py` skeleton, config keys in both packages, deps. **Wave A** (6 parallel worktree subagents, disjoint new files, octopus-merged): Story 2 MCP extensions (T1.1–T1.6) + agents T3.2/T3.3/T3.5/T3.7. orchestrator 114 + MCP 43 tests green, ruff clean. Conflict-free via pre-staging all shared mutable surfaces in Wave 0 (read-only after). **Wave B deferred**: Acquirer (T3.4) + Verifier (T3.6) — deps now merged, both unblocked.
- 2026-06-22: Rewrote 9 unpushed commit emails yahoo → GitHub noreply (`47295533+TSamarth@users.noreply.github.com`) to clear push protection; local `user.email` set to noreply; pushed origin/dev (fast-forward).