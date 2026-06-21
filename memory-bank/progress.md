# Progress

## Status: Story 1 + T0.4 (schema extension / Gate G1) done. 19 tests green. Pre-Story-2.

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
- [ ] **Story 2** — crawl4ai MCP extensions:
  - [ ] T1.1 eTLD+1 field (critical path)
  - [ ] T1.2 Content dedup (critical path, blocks Verifier)
  - [ ] T1.3 Seed-URL ingest
  - [ ] T1.4 PDF quality
  - [ ] T1.5 Rate-limit backoff
  - [ ] T1.6 Semantic Scholar citation graph API client (`orchestrator/app/citation.py`)
- [ ] **Story 3** — Agent layer:
  - [ ] Orchestrator + stage machine (with MID_ACQUIRE stage for CP3)
  - [ ] Clarifier agent
  - [ ] Planner agent
  - [ ] Acquirer agent (+citation BFS, 2-hop, relevance-gated, deep plans only)
  - [ ] Extractor agent (+publication_date propagation to chunk metadata)
  - [ ] Verifier agent (+contradiction confidence scoring, +temporal drift detection)
  - [ ] Writer agent (+temporal drift sub-section in contradictions appendix)
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
- google-adk NOT yet installed in venv (Story 1 used light deps only). Full `uv sync` needed before Story 3; verify ADK supports Python 3.13 (venv is 3.13).

## Decision Log
- 2026-06-18: Brainstorm complete. Framework left open. Verification = corroborate-or-flag. Checkpoints at plan + draft. Adaptive depth.
- 2026-06-19: Design — ADK+A2A, hierarchical coordinator, one hot 14B model. Workflow + task hierarchy produced.
- 2026-06-19: Story 1 implemented. Schemas use Pydantic v2 (ADK-native). New package at `orchestrator/` (sibling to `mcp/`). 14 tests green.
- 2026-06-21: T0.4 schema extension (Gate G1) done — added optional date/citation/temporal/contradiction-scoring fields + `Contradiction` model + 2 enums + config threshold. Backward compatible; 19 tests green. Unblocks Story 2.
- 2026-06-21: Competitive research (vs. Firecrawl) + brainstorm. Added P1 citation BFS (2-hop, relevance-gated, deep plans only), P2 contradiction confidence scoring, P4 temporal drift detection, P5 CP3 (deep only). Deferred P3 knowledge graph + P6 session comparison to Story 7. Schema extension needed pre-Story-2. T1.6 Semantic Scholar API client added to Story 2.