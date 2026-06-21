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
| 1.5 **Schema extension** (pre-Story-2): add `publication_date`, `citation_refs` to `ScoredURL`; `temporal_status` to `Claim`; `confidence_score`+`conflict_type` to contradiction records | T0.4 | next action |

**Gate G1** schemas frozen (after 1.5) · **G2** model fits VRAM (user runs 1.3).

---

## STORY 2 — crawl4ai MCP Extensions  `[parallel track, own files]`
Goal: dedup + provenance + robustness + Semantic Scholar client. Two tasks on critical path.

| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 2.1 eTLD+1 field in discover + chunk metadata | T1.1 | `/sc:implement` | **critical path** |
| 2.2 Content-similarity dedup (cosine ≥0.92) | T1.2 | `/sc:implement` | **critical path**, after 2.1 |
| 2.3 Seed-URL/file ingest path | T1.3 | `/sc:implement` | parallel |
| 2.4 PDF/arXiv extraction quality fix | T1.4 | `/sc:troubleshoot` → `/sc:improve` | parallel, independent |
| 2.5 Per-domain rate-limit + backoff | T1.5 | `/sc:implement` | parallel, independent |
| 2.6 **Semantic Scholar citation graph API client** (`orchestrator/app/citation.py`): `get_paper_metadata(id)` → `{publication_date, references, citers}`. httpx async, not an MCP tool. | T1.6 | `/sc:implement` | parallel, after 1.5 (schema ext) |
| 2.7 MCP extension tests (in-memory Client) + Semantic Scholar client tests | T5.2 | `/sc:test` | per-task |

**Gate G3** dedup collapses mirror set, eTLD+1 populated, seeds ingested, Semantic Scholar client returns metadata.

---

## STORY 3 — Agent Layer  `[parallel after S1]`
Goal: orchestrator + 6 specialists. Verifier is long pole.

| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 3.1 Orchestrator + stage machine skeleton (spike ADK A2A; includes MID_ACQUIRE stage for CP3) | T2.1 | `/sc:implement` | after S1; spike early |
| 3.2 Clarifier agent | T2.2 | `/sc:implement` | parallel |
| 3.3 Planner agent + adaptive depth | T2.3 | `/sc:implement` | parallel |
| 3.4 Acquirer agent (MCP discover+triage + citation BFS via T1.6 for academic+deep plans, 2-hop, relevance-gated) | T2.4 | `/sc:implement` | after 2.1, 2.3, 2.6 |
| 3.5 Extractor agent (MCP crawl_* + propagate `publication_date` from ScoredURL to chunk metadata) | T2.5 | `/sc:implement` | after 1.5 (schema ext) |
| 3.6 **Verifier** agent + independence test + ClaimLedger + contradiction confidence scoring (3-tier) + temporal drift detection | T2.6 | `/sc:implement` | **after 2.1+2.2** (blocked) |
| 3.7 Writer agent + coverage check + temporal drift sub-section | T2.7 | `/sc:implement` | after 1.5 |
| 3.8 Per-agent unit tests (mock model) | T5.1 | `/sc:test` | per-agent |

**Gate G4** each agent isolated-green.

---

## STORY 4 — Human-in-Loop Checkpoints  `[after orchestrator]`
| Task | Maps to | Delegate | Strategy |
|------|------|----------|----------|
| 4.1 Checkpoint CLI (approve/edit/reject, $EDITOR, blocking) | T3.1 | `/sc:implement` | after 3.1 |
| 4.2 Wire CP1 (plan) + CP2 (draft) into stage machine | T3.2 | `/sc:implement` | after 4.1, 3.3, 3.7 |
| 4.3 **CP3 mid-acquisition checkpoint** (`depth=deep` only): show source list + theme clusters; handle `[+]add/[-]exclude/[r]redirect` → supplemental Acquirer pass before Extractor | T3.3 | `/sc:implement` | after 4.1, 3.4, 3.1 |

**Gate G5** CP1/CP2 block until user acts (AC2); CP3 fires on deep plan, skips on shallow/normal.

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
WAVE 1  ║ [S1 DONE] S1.5 schema-ext │ S2.1 S2.3 S2.4 S2.5 S2.6(S2 API)  (parallel)
WAVE 2  ║ S2.2 dedup │ S3.2 S3.3 S3.5 S3.7                               (agents fan out)
WAVE 3  ║ S3.1 orchestrator │ S3.6 Verifier │ S4.1 CLI                   (Verifier unblocked)
WAVE 4  ║ S4.2 → S4.3(CP3) → S5.1 → S5.2 → S5.3                        (sequential integration)
WAVE 5  ║ S6.1 S6.2 (parallel) → S6.3                                    (validation)
WAVE 6  ║ S7.1 S7.2                                                       (post-MVP, deferred)
```

**Critical path:** S1.5 → S2.1 → S2.2 → S3.6 → S5.1 → S5.2 → S6.1.
S1.5 (schema ext, S effort) + S2.1 start together; S2.6 (Semantic Scholar client) parallel in Wave 1.

---

## Delegation Summary
| Command | Owns |
|---------|------|
| `/sc:implement` | all build tasks (S1.1/1.2/1.4, S2.1/2.2/2.3/2.5, all S3, S4, S5) |
| `/sc:test` | resource probe (1.3), MCP tests (2.6), agent units (3.8), acceptance (6.1), OOM (6.2) |
| `/sc:troubleshoot`+`/sc:improve` | PDF extraction quality (2.4) |
| `/sc:document` | final docs (6.3) |

---

**Next step**: execute Wave 1. Start `/sc:implement` on **S1.1 (schemas)** — head of critical path.