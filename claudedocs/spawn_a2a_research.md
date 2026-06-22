# Task Orchestration: A2A Deep Research Pipeline

Decomposition only. Each task delegates to a `/sc:*` command. Source: [workflow_a2a_research.md], [memory-bank/architecture.md].

**Coordination strategy: ADAPTIVE** — parallel within waves, sequential across the integration chain. Critical path gated by Verifier (T2.6 ← T1.1+T1.2).

---

## EPIC: Local A2A Deep Research Pipeline
One query → vetted markdown report. ADK + A2A, Ollama, crawl4ai MCP, 16 GB envelope.
Success = AC1–AC6 pass (clarify-or-run, two blocking checkpoints, structured report, contradiction flagging, uncorroborated marking, no OOM).

---

## STORY 1 — Foundation & Scaffold  `[DONE ✅]`
Goal: schemas + runnable ADK app + model verified. Unblocks everything.

| Task | Maps to (T) | Status |
|------|------|--------|
| 1.1 Define artifact schemas (ResearchPlan, ScoredURL, ClaimLedger, StageState) | T0.1 | ✅ DONE |
| 1.2 ADK app scaffold + config/env + pyproject | T0.2 | ✅ DONE |
| 1.3 Pull Qwen2.5-14B-Q4 + embed, verify VRAM < 14 GB | T0.2 | user must run probe |
| 1.4 Session store + resume (stage.json) | T0.3 | ✅ DONE |
| 1.5 **Schema extension** (pre-Story-2): `publication_date`+`citation_refs` on `ScoredURL`; `publication_date` on `SourceRef`; `temporal_status` on `Claim`; new `Contradiction` model w/ `confidence_score`+`conflict_type`; `ConflictType`/`TemporalStatus` enums; `TEMPORAL_DRIFT_THRESHOLD_MONTHS` config | T0.4 | ✅ DONE (19 tests, backward compat) |

**Gate G1** schemas frozen (after 1.5) — ✅ CLEARED · **G2** model fits VRAM (user runs 1.3).

---

## STORY 2 — crawl4ai MCP Extensions  `[DONE ✅ — Wave A, 2026-06-22]`
Goal: dedup + provenance + robustness + Semantic Scholar client. Two tasks on critical path.

| Task | Maps to | Delegate | Status / file |
|------|------|----------|----------|
| 2.1 eTLD+1 field in discover + chunk metadata | T1.1 | `/sc:implement` | ✅ `app/domain.py` + SQLite `chunks.etld1` |
| 2.2 Content-similarity dedup (cosine ≥0.92) | T1.2 | `/sc:implement` | ✅ `tools/dedup.py` + `crawled_pages.duplicate_of` |
| 2.3 Seed-URL/file ingest path | T1.3 | `/sc:implement` | ✅ `tools/seed.py` (registered in `common.py`) |
| 2.4 PDF/arXiv extraction quality fix | T1.4 | `/sc:troubleshoot` → `/sc:improve` | ✅ routing in `crawl.py`/`adaptive_crawl.py` |
| 2.5 Per-domain rate-limit + backoff | T1.5 | `/sc:implement` | ✅ `app/ratelimit.py` |
| 2.6 **Semantic Scholar citation graph API client** (`orchestrator/app/citation.py`): `get_paper_metadata(id)` → `{publication_date, references, citers}`. httpx async, not an MCP tool. | T1.6 | `/sc:implement` | ✅ `app/citation.py` (`CitationClient`, BFS, id parsing) |
| 2.7 MCP extension tests (in-memory Client) + Semantic Scholar client tests | T5.2 | `/sc:test` | ✅ `test_story2.py` (15) + `test_citation.py` (35) |

**Gate G3** dedup collapses mirror set, eTLD+1 populated, seeds ingested, Semantic Scholar client returns metadata — ✅ CLEARED (MCP 43 tests green; orchestrator 114 green).

---

## STORY 3 — Agent Layer  `[DONE ✅ — Wave 0 + Wave A + Wave B, 2026-06-22]`
Goal: orchestrator + 6 specialists. Verifier is long pole.

| Task | Maps to | Delegate | Status / file |
|------|------|----------|----------|
| 3.1 Orchestrator + stage machine skeleton (spike ADK A2A; includes MID_ACQUIRE stage for CP3) | T2.1 | `/sc:implement` | ✅ `orchestrator.py` + `stage_machine.py` + `llm.py` factory (Wave 0) |
| 3.2 Clarifier agent | T2.2 | `/sc:implement` | ✅ `agents/clarifier.py` (20 tests) |
| 3.3 Planner agent + adaptive depth | T2.3 | `/sc:implement` | ✅ `agents/planner.py` (13 tests) |
| 3.4 Acquirer agent (MCP discover+triage + citation BFS via T1.6 for academic+deep plans, 2-hop, relevance-gated) | T2.4 | `/sc:implement` | ✅ `agents/acquirer.py` (19 tests) — Wave B |
| 3.5 Extractor agent (MCP crawl_* + propagate `publication_date` from ScoredURL to chunk metadata) | T2.5 | `/sc:implement` | ✅ `agents/extractor.py`, date passthrough (9 tests) |
| 3.6 **Verifier** agent + independence test + ClaimLedger + contradiction confidence scoring (3-tier) + temporal drift detection | T2.6 | `/sc:implement` | ✅ `agents/verifier.py` (32 tests) — Wave B |
| 3.7 Writer agent + coverage check + temporal drift sub-section | T2.7 | `/sc:implement` | ✅ `agents/writer.py`, coverage + drift split (9 tests) |
| 3.8 Per-agent unit tests (mock model) | T5.1 | `/sc:test` | ✅ done per merged agent |

**Gate G4** each agent isolated-green — ✅ ALL six specialists green (3.1/3.2/3.3/3.4/3.5/3.6/3.7, offline mocked model). Wave B (3.4 + 3.6) merged 2026-06-22.

---

## STORY 4 — Human-in-Loop Checkpoints  `[DONE ✅ — 2026-06-22]`
| Task | Maps to | Delegate | Status / file |
|------|------|----------|----------|
| 4.1 Checkpoint CLI (approve/edit/reject, $EDITOR, blocking) | T3.1 | `/sc:implement` | ✅ `app/checkpoint.py` (`cp1/cp2/cp3_checkpoint` + `Handler` wrappers, `CheckpointRejected`; 11 tests) |
| 4.2 Wire CP1 (plan) + CP2 (draft) into stage machine | T3.2 | `/sc:implement` | ✅ `post_handlers` in `orchestrator.step()` (CP1→PLAN, CP2→WRITE) |
| 4.3 **CP3 mid-acquisition checkpoint** (`depth=deep` only): source list + `[+]add/[-]exclude/[r]redirect` → supplemental Acquirer pass before Extractor | T3.3 | `/sc:implement` | ✅ `phase_handlers` in `_run_research_pass()` (CP3→MID_ACQUIRE) + `SessionStore.save/load_scored_urls` |

**Gate G5** CP1/CP2 block until user acts (AC2); CP3 fires on deep plan, skips on shallow/normal — ✅ CLEARED (`test_cp1/cp2_blocks_until_approve`, `test_cp3_skips_on_shallow_plan` + fires-on-deep; orchestrator 181 tests green).
**Carry-forward**: checkpoint handlers register into the live composition root at Story 5 (T4.2) alongside real agent wiring — skeleton orchestrator still uses stub handlers, so the mechanism + handlers are landed and G5-tested but not yet active in an end-to-end run.

---

## STORY 5 — Integration & Research Loop  `[sequential chain]`
| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 5.1 Research loop (Acquire→Extract→Verify, stop-rule, budget caps) | T4.1 | `/sc:implement` | seq, after S3 core |
| 5.2 Full pipeline integration (all stages) | T4.2 | `/sc:implement` | seq, after 5.1, S4 |
| 5.3 Adaptive-depth tie-in (plan depth → loop budgets) | T4.3 | `/sc:implement` | seq, after 5.2 |

---

## STORY 6 — QA & Validation  `[after integration]`
| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 6.1 E2E acceptance AC1–AC7 (incl. temporal drift case + CP3 deep-only gate) | T5.3 | `/sc:test` | after 5.2 |
| 6.2 Resource/OOM + VRAM ceiling AC6 (incl. citation BFS RAM overhead) | T5.4 | `/sc:test` | after 5.2 |
| 6.3 Final docs (README, run guide) | — | `/sc:document` | after 6.1 |

**Gate G6** AC1–AC7 pass · **G7** no OOM.

---

## STORY 7 — Post-MVP Extensions  `[after Story 6 acceptance]`
Goal: knowledge graph + session comparison. Not in v1 scope.

| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 7.1 Knowledge graph extraction: post-Extractor NER + relation extraction → NetworkX + SQLite graph (`entities`, `relations` tables) | T7.1 | `/sc:implement` | after G6 |
| 7.2 Research session comparison: cross-run claim diff via ChromaDB cosine; structured delta report | T7.2 | `/sc:implement` | after G6 + ≥2 sessions |

No gates defined yet — scope and design TBD when MVP is stable.

---

## Execution Waves (adaptive coordination)

```
WAVE 1  ║ [S1 + S1.5 DONE ✅] │ [S2.1 S2.3 S2.4 S2.5 S2.6 DONE ✅]        (G1/G3 cleared)
WAVE 2  ║ [S2.2 dedup │ S3.2 S3.3 S3.5 S3.7 DONE ✅]                       (agents fanned out)
WAVE 3  ║ [S3.1 orchestrator │ S3.6 Verifier │ S4.1 CLI DONE ✅]          (G4/G5 cleared)
WAVE 4  ║ [S4.2 S4.3(CP3) DONE ✅] → S5.1 → S5.2 → S5.3  (head now)        (sequential integration)
WAVE 5  ║ S6.1 S6.2 (parallel) → S6.3                                    (validation)
WAVE 6  ║ S7.1 S7.2                                                       (post-MVP, deferred)
```

**Critical path:** ~~S1.5~~ ✅ → ~~S2.1~~ ✅ → ~~S2.2~~ ✅ → ~~S3.6~~ ✅ → **S5.1** (head now) → S5.2 → S6.1.
Stories 1–4 done (G1/G3/G4/G5 cleared, schemas frozen). Remaining critical path is the sequential integration chain: research loop (S5.1) → full integration incl. live checkpoint registration (S5.2) → E2E acceptance (S6.1). G2 (VRAM probe) still owed by user.

---

## Delegation Summary
| Command | Owns |
|---------|------|
| `/sc:implement` | all build tasks (S1.1/1.2/1.4, S2.1/2.2/2.3/2.5, all S3, S4, S5) |
| `/sc:test` | resource probe (1.3), MCP tests (2.6), agent units (3.8), acceptance (6.1), OOM (6.2) |
| `/sc:troubleshoot`+`/sc:improve` | PDF extraction quality (2.4) |
| `/sc:document` | final docs (6.3) |

---

**Next step**: Stories 1–4 ✅ done (G1/G3/G4/G5 cleared, orchestrator 181 + MCP 43 tests green, pushed origin/dev). Start `/sc:implement` on **S5.1 (research loop)** — head of the remaining critical path: wire Acquire→[CP3]→Extract→Verify inside `Stage.RESEARCH` with stop-rule + budget caps, then S5.2 (full integration, incl. registering the Story-4 checkpoint handlers into the composition root). User still owes S1.3 probe (G2, VRAM).