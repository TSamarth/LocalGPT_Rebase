# System Patterns

> Architecture DESIGNED. Full spec in [architecture.md]. This file = the durable patterns.

## Framework
**Google ADK + A2A protocol** (kept, not swapped). Rationale: existing crawl4ai MCP already integrates with ADK `MCPToolset`; ADK gives hierarchical agents + A2A + MCP + session/human-in-loop natively; framework is RAM-neutral (cost is the models). See [architecture.md] §0.

## Topology: Hierarchical Coordinator
Orchestrator (root) delegates to single-responsibility specialists. Specialists never call each other — all routing through orchestrator.

```
Orchestrator (control only: session, stage machine, checkpoints, budget, stop-rule)
 ├─ Clarifier  (ambiguity + missing-constraint detection)
 ├─ Planner    (subtopic decomposition, adaptive depth)
 ├─ Acquirer   (MCP: discover_urls + score_and_triage_urls + citation BFS if deep)  ← MCP + S2 API
 ├─ Extractor  (MCP: crawl_* + publication_date passthrough)                         ← MCP access
 ├─ Verifier   (search_chunks → claims → corroborate/flag + confidence + temporal)
 └─ Writer     (ClaimLedger → structured markdown + temporal drift section)
```

## Core Patterns
1. **One hot model, many roles** — single Qwen2.5-14B Q4 (8B fallback) role-prompted per agent + resident nomic-embed-text. No model swap. (§1)
2. **Sequential stages** — forced by 16 GB RAM. Never heavy crawl + big inference concurrently. Structural, not a runtime guard.
3. **Typed artifact handoffs, IDs not text** — agents pass `ResearchPlan` / `ScoredURL[]` / `ClaimLedger` / `page_ids`; crawled content stays in SQLite/Chroma. RAM + token discipline.
4. **Least-privilege tools** — only Acquirer/Extractor hold MCP write tools. Semantic Scholar API client lives in `orchestrator/` (not MCP), called directly by Acquirer.
5. **Deterministic control, LLM content** — stage machine wraps LLM calls; LLMs decide content, not control flow.
6. **Verification = corroborate-or-flag** — ≥2 *independent* sources (different eTLD+1 AND content cosine < 0.92) → keep; contradiction → flag both sides; single → uncorroborated.
7. **Blocking human-in-loop gates** — CP1 (plan), CP2 (draft). CLI-first, `$EDITOR` file-fallback.
8. **Citation BFS with relevance gate** — Acquirer runs 2-hop BFS over Semantic Scholar citation graph for academic sources on `depth=deep` plans. LLM relevance-gates each candidate before enqueue. Bounded by hop depth + per-subtopic budget cap.
9. **Contradiction confidence scoring** — three-tier rubric (source independence + recency delta + methodological explicitness). Each contradiction emits `confidence_score: float` + `conflict_type`. Appendix sorted by score descending.
10. **Temporal drift detection** — if contradicting sources have publication date delta ≥ 18 months (configurable), classified as `temporal_drift` not `factual`. Surfaced in separate "Temporal Drift" sub-section; older claim tagged `temporal_status: "dated"`.
11. **CP3 = depth-gated mid-acquisition checkpoint** — fires after Acquirer, before Extractor, only on `depth=deep` plans. User may steer source selection without full re-plan.

## Stage Machine
INTAKE → CLARIFY → PLAN → ★CP1 → [ACQUIRE → ★CP3(deep only) → EXTRACT → VERIFY loop] → SYNTHESIZE → ★CP2 → WRITE.
Stop-rule per subtopic: target_evidence met OR diminishing returns (<N new claims) OR iteration cap.
MID_ACQUIRE stage: live between ACQUIRE and EXTRACT when `plan.depth == "deep"`.

## State
ADK session state (live) + existing SQLite (content/provenance + publication_date in chunk metadata) + `data/sessions/{id}/` artifacts (plan.json, claim_ledger.json, draft.md, stage.json). Resume = reload stage.json, skip done stages, reuse crawled content.

## Reuse vs Build
crawl4ai MCP pipeline (discover→triage→crawl→search, SQLite+Chroma+Ollama) reused as-is. 6 extensions planned: content-sim dedup, eTLD+1 field, seed-URL ingest, PDF quality, rate-limit backoff, Semantic Scholar API client (orchestrator utility). See [architecture.md] §7.

## Deferred (Story 7)
Knowledge graph extraction (NER + relation graph, NetworkX/SQLite). Research session comparison (cross-run claim diff). Neither in scope until core MVP validated.