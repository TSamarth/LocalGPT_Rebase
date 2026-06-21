# Requirements Specification: A2A Deep Research Pipeline

Status: APPROVED (brainstorm phase). Architecture deferred to `/sc:design`.

## Functional Requirements

### FR1 — Query Intake & Interpretation
- FR1.1 Accept query as one-liner or multi-paragraph description.
- FR1.2 Detect ambiguous scope (multiple plausible interpretations) and missing constraints (timeframe, region, version, audience, etc.).
- FR1.3 If FR1.2 triggers → STOP and ask user follow-up. Else proceed autonomously.
- FR1.4 Clarification before run start only — mid-run pauses are approvals, not new clarifications.

### FR2 — Research Planning
- FR2.1 Decompose query into sub-topics covering "all aspects."
- FR2.2 Scale effort to query complexity (adaptive depth).
- FR2.3 Produce a research plan (sub-topics, source strategy, est. effort).
- FR2.4 **Checkpoint 1**: present plan to user for approval/edit before execution.
- FR2.5 **Checkpoint 3 (CP3)**: for `depth=deep` plans only, present source list + initial theme clusters after Acquirer completes, before Extractor crawls. User may add terms, exclude URLs, or redirect subtopics. Skipped for shallow/normal plans.

### FR3 — Source Acquisition (crawl4ai MCP)
- FR3.1 Aggregate multi-source URLs: SerpAPI + DuckDuckGo + arXiv (existing).
- FR3.2 Source classes: open web, academic/papers (PDF parse), code/technical (GitHub/docs/SO), user-provided URLs/files.
- FR3.3 Crawl + extract clean content per acquired URL.
- FR3.4 Extensible: new source connectors addable without core rewrite.
- FR3.5 Citation network traversal: for academic sources on `depth=deep` plans, Acquirer follows Semantic Scholar citation graph 2 hops deep. Relevance-gated (LLM scores candidate papers against subtopic question). BFS bounded by hop depth + per-subtopic budget cap.

### FR4 — Agent Orchestration & Delegation
- FR4.1 A2A architecture: specialized agents per stage (planner, searcher, extractor, verifier, writer).
- FR4.2 Delegate only at stages requiring it — minimize concurrent model load.
- FR4.3 Stage handoffs pass structured intermediate artifacts, not raw context dumps.

### FR5 — Verification
- FR5.1 Claim corroborated by 2+ independent sources → keep.
- FR5.2 Claim contradicted across sources → flag (retain both sides, do not silently pick).
- FR5.3 Every kept claim traceable to source URL(s).
- FR5.4 Contradictions surfaced in report, not discarded.
- FR5.5 Contradiction confidence scoring: each flagged contradiction carries `confidence_score` (three-tier rubric: source independence + recency delta + methodological explicitness) and `conflict_type` (factual | methodological | temporal_drift). Contradictions appendix sorted by score descending.
- FR5.6 Temporal claim dating: where source `publication_date` is available, detect temporal drift (date delta ≥ `temporal_drift_threshold`, default 18 months). Classify as `temporal_drift` not hard contradiction. Tag claims `temporal_status`. Surfaced in dedicated "Temporal Drift" sub-section of contradictions appendix.

### FR6 — Report Generation
- FR6.1 Single markdown file per session.
- FR6.2 Structure: exec summary → section per sub-topic → sources → contradictions appendix.
- FR6.3 Coverage check: report addresses all aspects from FR2.1.
- FR6.4 **Checkpoint 2**: present draft for approval before final write.

## Non-Functional Requirements
- **NFR1 Hardware (hard)**: 16 GB VRAM / Ryzen 5 3600 / 16 GB RAM. Resident models fit VRAM; RAM tight → sequential stage loading expected.
- **NFR2 Resource efficiency**: no simultaneous large-model loads; prefer stage-sequential or one shared role-prompted model. Crawl concurrency bounded to RAM.
- **NFR3 Local-first/privacy**: all inference local (Ollama); only outbound = source fetch.
- **NFR4 Framework**: open to recommendation; must support A2A + local Ollama + MCP within envelope. Decision deferred to design.
- **NFR5 Output**: every completed session yields exactly one vetted markdown artifact.

## User Stories
- US1: One-liner triggers follow-ups only when scope ambiguous / constraints missing; else just runs.
- US2: User approves research plan before compute spent.
- US3: User reviews draft before finalize.
- US4: Report — every claim sourced, multi-source kept, conflicts flagged.
- US5: User can seed specific URLs/files a run must include.

## Acceptance Criteria
- AC1: Ambiguous one-liner ("jaguar performance") triggers follow-up; clear detailed query does not.
- AC2: Plan checkpoint (CP1) and draft checkpoint (CP2) both block until user acts.
- AC3: Final markdown contains exec summary, per-subtopic sections, source list, contradictions appendix (with Temporal Drift sub-section).
- AC4: Planted cross-source contradiction appears flagged, not silently resolved.
- AC5: Single-source unverified claim dropped or marked uncorroborated.
- AC6: End-to-end run does not exceed 16 GB VRAM (no OOM).
- AC7: Planted temporal drift case (old source contradicted by newer source, date delta ≥ 18mo) classified as `temporal_drift`, appears in "Temporal Drift" sub-section, not in "Factual Contradictions".

## Open Questions (design phase)
1. Model fit: single mid-size role-prompted model vs. small specialized models per agent? (16 GB RAM is bottleneck.)
2. "Independent source" definition — detect mirrored/syndicated/copied content.
3. crawl4ai MCP gaps — dedup? PDF extraction quality? more search backends? rate-limit handling?
4. Session persistence — resume interrupted run? store intermediate artifacts where?
5. Coverage measurement — stopping criterion for "all aspects covered."
6. Checkpoint UX — CLI prompt, file-based, or web UI?