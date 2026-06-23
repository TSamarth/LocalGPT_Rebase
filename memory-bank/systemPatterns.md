# System Patterns

> Architecture DESIGNED (**v2 ratified 2026-06-23**). Full spec in [architecture.md]. This file = the durable patterns. Code is at v1 (hand-rolled orchestrator); v1→v2 migration = architecture.md §15.

## Framework
**Google ADK 2.x + A2A protocol** (committed; pin `google-adk>=2.3,<3`). Rationale: existing crawl4ai MCP already integrates with ADK `MCPToolset`; ADK 2.0 gives **dynamic workflows** + **native session/resume** + **`RequestInput` HITL** + first-class **A2A** + MCP — exactly the primitives the v1 design hand-rolled; framework is RAM-neutral (cost is the models). See [architecture.md] §0/§13/§14.

## Topology: Dynamic Workflow + Local Nodes Under One A2A Boundary
A dynamic-workflow root (`@node`) composes single-responsibility specialist **nodes**. Specialists are **local sub-agents** (in-process, one hot model), never call each other, never A2A peers — data flows through the workflow. The whole pipeline is the one A2A-exposed agent (local-first).

```
localhost A2A client / RemoteA2aAgent ──A2A──▶ A2AServer (adk api_server --a2a)
                                                  └─ Workflow root (@node research)  ← ADK owns session+resume
                                                       ├─ Clarifier  (ambiguity + missing-constraint detection)
                                                       ├─ Planner    (subtopic decomposition, adaptive depth)
                                                       ├─ Acquirer   (MCP: discover + triage + citation BFS if deep) ← MCP + S2 API
                                                       ├─ Extractor  (MCP: crawl_* + publication_date passthrough)   ← MCP write
                                                       ├─ Verifier   (search_chunks → claims → corroborate/flag + confidence + temporal) ← MCP read
                                                       └─ Writer     (ClaimLedger → structured markdown + temporal drift section)
```

## Core Patterns
1. **One hot model, many roles** — single Qwen2.5-14B Q4 (8B fallback) role-prompted per node + resident nomic-embed-text. No model swap. Set `OLLAMA_API_BASE` env var. (§1)
2. **Sequential stages** — forced by 16 GB RAM. Never heavy crawl + big inference concurrently. Structural, not a runtime guard. (Substrate supports `asyncio.gather` fan-out if hardware ever grows — §14.)
3. **Typed artifact handoffs, IDs not text** — nodes pass `ResearchPlan` / `ScoredURL[]` / `ClaimLedger` / `page_ids`; crawled content stays in SQLite/Chroma. RAM + token discipline. v2: `ctx.run_node()` returns the artifact directly (no session extraction).
4. **Least-privilege tools** — only Acquirer/Extractor/Verifier hold MCP tools; built once per run + `close()`d (no per-pass subprocess churn). Semantic Scholar API client lives in `orchestrator/` (not MCP), called directly by Acquirer. MCP stays the tool boundary — not migrated to A2A.
5. **Deterministic control, LLM content** — the dynamic workflow + pure `stop_rule`/`depth_budget` wrap LLM calls; LLMs decide content, not control flow. Exceptions propagate to ADK `RetryConfig`; never catch `BaseException` (breaks HITL pause).
6. **Verification = corroborate-or-flag** — ≥2 *independent* sources (different eTLD+1 AND content cosine < 0.92) → keep; contradiction → flag both sides; single → uncorroborated.
7. **Blocking human-in-loop gates** — CP1 (plan), CP2 (draft), CP3 (deep-only source steer). v2: ADK `RequestInput` nodes, resumable over `adk api_server`/SSE (v1 was console `input()`/`$EDITOR`). `response_schema` doesn't auto-coerce free input → CP3 needs a small normalizing adapter.
8. **Citation BFS with relevance gate** — Acquirer runs 2-hop BFS over Semantic Scholar citation graph for academic sources on `depth=deep` plans. LLM relevance-gates each candidate before enqueue. Bounded by hop depth + per-subtopic budget cap.
9. **Contradiction confidence scoring** — three-tier rubric (source independence + recency delta + methodological explicitness). Each contradiction emits `confidence_score: float` + `conflict_type`. Appendix sorted by score descending.
10. **Temporal drift detection** — if contradicting sources have publication date delta ≥ 18 months (configurable), classified as `temporal_drift` not `factual`. Surfaced in separate "Temporal Drift" sub-section; older claim tagged `temporal_status: "dated"`.
11. **CP3 = depth-gated mid-acquisition checkpoint** — fires after Acquirer, before Extractor, only on `depth=deep` plans. User may steer source selection without full re-plan.

## Stages (workflow positions + shared vocabulary)
INTAKE → CLARIFY → PLAN → ★CP1 → [ACQUIRE → ★CP3(deep only) → EXTRACT → VERIFY loop] → SYNTHESIZE → ★CP2 → WRITE → DONE.
Stop-rule per subtopic: target_evidence met OR diminishing returns (<N new claims) OR iteration cap (pure `stop_rule`/`depth_budget`, port to v2 unchanged).
MID_ACQUIRE (CP3): between ACQUIRE and EXTRACT when `plan.depth == "deep"`. v2: these are positions in the `@node` workflow graph, not entries in a hand-walked enum.

## State (v2: ADK-owned)
**Source of truth = ADK session + event store**; resume via `App(resumability_config=ResumabilityConfig(is_resumable=True))` + `invocation_id` (dynamic workflows auto-skip completed nodes on resume; tools may run ≥1× — crawl/extract idempotent by URL). Existing SQLite/Chroma hold content/provenance + publication_date (unchanged). `data/sessions/{id}/*.json` (plan/claim_ledger/draft) = **write-through export** via callback/plugin, not the resume engine; `stage.json` retired. (v1 used `SessionStore` as truth + stage.json resume — see architecture.md §5/§15.)

## Reuse vs Build
crawl4ai MCP pipeline (discover→triage→crawl→search, SQLite+Chroma+Ollama) reused as-is. 6 extensions planned: content-sim dedup, eTLD+1 field, seed-URL ingest, PDF quality, rate-limit backoff, Semantic Scholar API client (orchestrator utility). See [architecture.md] §7.

## Deferred (Story 7)
Knowledge graph extraction (NER + relation graph, NetworkX/SQLite). Research session comparison (cross-run claim diff). Neither in scope until core MVP validated.