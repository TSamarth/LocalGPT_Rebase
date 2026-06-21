# Implementation Workflow: A2A Deep Research Pipeline

Source specs: [memory-bank/requirements.md], [memory-bank/architecture.md]. Plan only — no code executed.

---

## 1. Spec Through Different Lenses

Same spec, six viewpoints. Each lens owns a slice; overlaps flagged.

| Lens | Owns | Key concerns | Spec refs |
|------|------|--------------|-----------|
| **Backend / orchestration** | Orchestrator, stage machine, agents, artifact schemas, session store | Control flow correctness, typed handoffs, resume | FR1–6, NFR5 |
| **MCP / data acquisition** | crawl4ai MCP + 5 extensions | Source coverage, dedup, provenance, robustness | FR3, arch §7 |
| **ML / inference** | Ollama model strategy, embeddings, independence test | VRAM fit, no swap thrash, cosine thresholds | NFR1–2, arch §1 |
| **Verification / trust** | Verifier, ClaimLedger, corroborate-or-flag | Claim accuracy, independence detection, no silent resolve | FR5, AC4–5 |
| **UX / CLI** | Checkpoint gates, plan/draft edit flow | Blocking gates, $EDITOR loop, clarity | FR2.4, FR6.4, AC2 |
| **QA / devops** | Tests, E2E acceptance, OOM/resource guard, scaffold | AC1–6, sequential-stage enforcement | All AC, NFR1 |

**Cross-lens hot spots** (need coordination, not solo):
- Verifier (trust lens) ⟂ MCP (dedup/eTLD+1) ⟂ ML (cosine threshold). → independence test is a 3-lens contract.
- Orchestrator (backend) ⟂ ML (sequential model loading) ⟂ QA (OOM). → resource discipline is structural.

---

## 2. Task Breakdown

ID · task · lens · effort (S/M/L) · depends-on.

### Phase 0 — Foundation (start immediately, parallel)
| ID | Task | Lens | Effort | Deps | Status |
|----|------|------|--------|------|--------|
| T0.1 | Artifact schemas (Pydantic): `ResearchPlan`, `ScoredURL`, `ClaimLedger`, `StageState` | backend | M | — | ✅ DONE |
| T0.2 | Project scaffold: ADK app structure, config/env, `pyproject`, pull Qwen2.5-14B-Q4 + verify VRAM | devops/ML | M | — | ✅ DONE |
| T0.3 | Session store + resume (`data/sessions/{id}/`, stage.json read/write/skip-done) | backend | M | T0.1 | ✅ DONE |
| T0.4 | **Schema extension** (pre-Story-2): add optional `publication_date`+`citation_refs` to `ScoredURL`; `temporal_status` to `Claim`; `confidence_score`+`conflict_type` to contradiction records | backend | S | T0.1 | — |

### Phase 1 — crawl4ai MCP extensions (parallel track, independent of agent layer)
| ID | Task | Lens | Effort | Deps |
|----|------|------|--------|------|
| T1.1 | eTLD+1 field in `discover_urls` output + chunk metadata | MCP | S | — |
| T1.2 | Content-similarity dedup pass (cosine ≥ 0.92, collapse mirrors/syndication) | MCP/ML | M | T1.1 |
| T1.3 | Seed-URL/file ingest path (force user URLs into crawl set) | MCP | S | — |
| T1.4 | PDF/arXiv extraction quality validation + fix if lossy | MCP | M | — |
| T1.5 | Per-domain rate-limit + backoff (429 retry) | MCP | S | — |
| T1.6 | Semantic Scholar citation graph API client (`orchestrator/app/citation.py`): `get_paper_metadata(id)` → `publication_date`, `references[]`, `citers[]`. httpx utility, not MCP tool. | backend | S | T0.4 |

### Phase 2 — Agent layer (depends Phase 0 schemas + model)
| ID | Task | Lens | Effort | Deps |
|----|------|------|--------|------|
| T2.1 | Orchestrator + stage machine skeleton (deterministic, mock stages; includes MID_ACQUIRE stage for CP3) | backend | L | T0.1, T0.3 |
| T2.2 | Clarifier agent (ambiguity + missing-constraint detection) | backend/ML | M | T0.1, T0.2 |
| T2.3 | Planner agent → `ResearchPlan` + adaptive depth | backend/ML | M | T0.1, T0.2 |
| T2.4 | Acquirer agent (MCP `discover_urls`+`score_and_triage_urls` + citation BFS via T1.6 for deep academic) | backend/MCP | M | T0.4, T1.1, T1.3, T1.6 |
| T2.5 | Extractor agent (MCP `crawl_*` + propagate `publication_date` from `ScoredURL` to chunk metadata) | backend/MCP | M | T0.4 |
| T2.6 | **Verifier** agent + independence test + `ClaimLedger` + contradiction confidence scoring (3-tier rubric) + temporal drift detection | trust/ML | L | T0.4, **T1.1, T1.2** |
| T2.7 | Writer agent → structured markdown + coverage check + temporal drift sub-section in contradictions appendix | backend | M | T0.4 |

### Phase 3 — Human-in-loop checkpoints
| ID | Task | Lens | Effort | Deps |
|----|------|------|--------|------|
| T3.1 | Checkpoint CLI (approve/edit/reject, `$EDITOR`, blocking) | UX | M | T2.1 |
| T3.2 | Wire CP1 (plan) + CP2 (draft) into stage machine | UX/backend | S | T3.1, T2.3, T2.7 |
| T3.3 | CP3 mid-acquisition checkpoint (`depth=deep` only): show source list + theme clusters; handle `[+]add/[-]exclude/[r]redirect` → supplemental Acquirer pass | UX/backend | M | T3.1, T2.4, T2.1 |

### Phase 4 — Integration
| ID | Task | Lens | Effort | Deps |
|----|------|------|--------|------|
| T4.1 | Research loop wiring (Acquire→[CP3]→Extract→Verify, stop-rule, budget caps) | backend | L | T2.1, T2.4, T2.5, T2.6, T3.3 |
| T4.2 | Full pipeline integration (all stages end-to-end) | backend | M | T4.1, T2.2, T2.3, T2.7, T3.2 |
| T4.3 | Adaptive-depth tie-in (plan depth → loop budgets/target_evidence/citation_bfs_enabled/cp3_enabled) | backend/ML | S | T2.3, T4.1 |

### Phase 5 — QA / validation
| ID | Task | Lens | Effort | Deps |
|----|------|------|--------|------|
| T5.1 | Unit tests per agent (mock model) | QA | M | each T2.x |
| T5.2 | MCP extension tests (in-memory Client) | QA | M | T1.x |
| T5.3 | E2E acceptance AC1–AC7 (clarify, checkpoints incl. CP3, report shape, contradiction flag, uncorroborated, temporal drift) | QA | L | T4.2 |
| T5.4 | Resource/OOM validation AC6 (VRAM ceiling, sequential-stage proof, citation BFS RAM overhead) | QA/devops | M | T4.2 |

---

## 3. Dependency Graph

```
T0.1 schemas ──┬─> T0.3 session store ──┐
               │                         ├─> T2.1 orchestrator ─┐
               ├─> T2.2 clarifier        │                      │
               ├─> T2.3 planner ─────────┼──────────────┐       │
               ├─> T2.5 extractor        │              │       │
               ├─> T2.7 writer ──────────┼──────────┐   │       │
               │                         │          │   │       │
T0.2 scaffold/model ─> (T2.2,2.3 need)   │          │   │       │
                                         │          │   │       │
T1.1 eTLD+1 ─┬─> T1.2 dedup ─┐           │          │   │       │
             │               └─> T2.6 verifier ─────┼───┼───────┤
             └─> T2.4 acquirer (also T1.3) ─────────┼───┼───────┤
T1.3 seed ───┘                                      │   │       │
T1.4 pdf  (independent)                             │   │       │
T1.5 backoff (independent)                          │   │       │
                                                    ▼   ▼       ▼
                                         T3.1 CLI ─> T3.2 wire CP1/CP2
                                                            │
                          T4.1 research loop <──────────────┤
                                  │                         │
                                  └─> T4.2 full integration <┤
                                            │
                                  T4.3 adaptive depth
                                            │
                          ┌─────────────────┼──────────────┐
                       T5.3 E2E          T5.4 OOM        (T5.1/T5.2 ride alongside)
```

---

## 4. Parallel Execution Plan

**Wave 1 (day-1 parallel, no deps):** T0.1, T0.2, T1.1, T1.3, T1.4, T1.5
- Two independent tracks: Foundation (T0.x) + MCP extensions (T1.x). Different files, zero conflict.

**Wave 2 (after T0.1 + T0.2 + T1.1):** T0.3, T1.2, T2.2, T2.3, T2.4, T2.5, T2.7
- All six agents *except Verifier* can build in parallel once schemas + model land. T1.2 (dedup) runs alongside.

**Wave 3 (after T2.1 deps + T1.2):** T2.1, T2.6, T3.1
- Orchestrator skeleton, Verifier (now unblocked by dedup+eTLD), Checkpoint CLI.

**Wave 4 (integration, sequential-ish):** T3.2 → T4.1 → T4.2 → T4.3

**Wave 5 (QA):** T5.1/T5.2 fold into each build wave; T5.3/T5.4 after T4.2.

**Sequential-only (cannot parallelize):**
- T4.1 → T4.2 → T4.3 (each consumes prior's wiring).
- T2.6 must wait T1.2 (independence test needs dedup) — **the one cross-track dependency to watch.**

---

## 5. Critical Path

```
T0.4 → T1.6 → T2.4(BFS)  ← citation BFS track (parallel)
T0.1 → T1.1 → T1.2 → T2.6 → T4.1 → T4.2 → T5.3
       (schemas → eTLD+1 → dedup → Verifier → loop → integration → acceptance)
```

Critical path unchanged. T0.4 (schema extension) + T1.6 (Semantic Scholar client) run in parallel with T1.1+T1.2. Verifier (T2.6) still the long pole. **Front-load T0.4 + T1.1 + T1.2** simultaneously — T0.4 is small (S), done in parallel with MCP work.

---

## 6. Quality Gates

| Gate | After | Pass criteria |
|------|-------|---------------|
| G1 schemas frozen | T0.4 | All artifact contracts (incl. new optional fields) type-check; agents code against them. |
| G2 model fits | T0.2 | Qwen2.5-14B-Q4 + embed loaded, VRAM < 14 GB measured (NFR1). |
| G3 MCP extensions green | T1.x, T5.2 | dedup collapses known mirror set; eTLD+1 populated; seed URLs ingested; Semantic Scholar client returns metadata. |
| G4 each agent isolated-green | T2.x, T5.1 | Each agent unit-tested with mocked model + fixture artifacts. |
| G5 checkpoints block | T3.2, T3.3 | CP1/CP2 block until user acts (AC2); CP3 fires on deep plan + skips on normal. |
| G6 acceptance | T5.3 | AC1–AC7 pass on real query (incl. temporal drift case). |
| G7 resource | T5.4 | End-to-end run, no OOM, VRAM ceiling held (AC6). |

---

## 7. Risks → Mitigation
- **Verifier independence test brittle** (syndication slips through) → tune cosine threshold against a labeled mirror set in T1.2; treat 0.92 as starting value, not fixed.
- **VRAM overrun with long context** → cap context window in T0.2; measure at G2 before building agents on it.
- **RAM OOM** (crawl + inference overlap) → sequential stage machine is the guard; verify explicitly at T5.4.
- **ADK A2A + MCP wiring unknowns** → spike during T2.1 skeleton; isolate ADK quirks before the agent fan-out (Wave 2).
- **Stop-rule runaway** → hard iteration caps in T4.1 from day one.
- **Citation BFS unbounded** → relevance gate + per-subtopic BFS budget cap in config; test at T5.4.
- **Semantic Scholar rate limits** → 100 req/s unauthenticated; deep BFS on one subtopic is well within. Add 1s delay between hops as courtesy.
- **publication_date missing for web sources** → fallback to `temporal_status: "uncertain"`; temporal drift only active when both sources have dates.

---

## 8. Suggested Build Order (single-dev, resource-bounded)
1. **T0.4 schema extension** (S, immediate — unblocks T2.4/T2.5/T2.6 extended behavior) + **T0.2 probe** (verify VRAM, G2).
2. **T1.1 + T1.2** (critical path; unblocks Verifier) + **T1.6** (Semantic Scholar client, S effort, parallel).
3. **T2.1 orchestrator skeleton** (spike ADK A2A, includes MID_ACQUIRE stage).
4. Fan out agents: **T2.3 → T2.4 → T2.5 → T2.2 → T2.7**, then **T2.6** (after T1.2).
5. **T3.1 + T3.2 + T3.3** checkpoints (CP1/CP2/CP3).
6. **T4.1 → T4.2 → T4.3** integration.
7. **T5.3 + T5.4** acceptance (AC1–AC7) + resource.
(Remaining MCP T1.3/1.4/1.5 slot into any idle gap — independent.)

---

## 9. Deferred — Story 7 (post-MVP)
Not in current scope. Build only after Story 6 acceptance gates pass.

| ID | Task | Depends |
|----|------|---------|
| T7.1 | Knowledge graph extraction: post-Extractor NER + relation extraction → NetworkX + SQLite graph | Stable Story 6 |
| T7.2 | Research session comparison: cross-run claim diff via ChromaDB cosine, structured delta report | Stable Story 6, ≥2 sessions |

---

**Next step**: `/sc:implement` to execute. Recommended first target: **T0.1 (schemas)** then **T1.1+T1.2 (MCP dedup/eTLD+1)** — the critical path head.