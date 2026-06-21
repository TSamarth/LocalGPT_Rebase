# Active Context

## Current Phase
**Story 1 + T0.4 (Gate G1) — DONE + tested.** Schemas, config, session store, scaffold + schema extension (publication_date/citation_refs/temporal_status + Contradiction scoring model + ConflictType/TemporalStatus enums + TEMPORAL_DRIFT_THRESHOLD_MONTHS). All additions optional + backward compatible. 19 tests green. Gate G1 cleared → Story 2 (MCP extensions) unblocked, on critical path.

## Current Focus
`orchestrator/` package created (sibling to `mcp/`). Foundation = `app/schemas.py` (Pydantic contracts), `app/config.py`, `app/session.py` (create/resume + artifact persistence). Validated via local .venv + main.py smoke run.

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

## Next Steps
1. **Schema extension** (pre-Story-2, immediate): add optional fields to `ScoredURL` (`publication_date`, `citation_refs`) and `Claim` (`temporal_status`); add `confidence_score` + `conflict_type` to contradiction records. All optional with defaults → 14 existing tests still pass.
2. **Run setup probe on real hardware**: `cd orchestrator && python scripts/check_setup.py` — pull Qwen2.5-14B-Q4 (or 8B fallback) + nomic-embed-text; confirm VRAM < 14 GB (Gate G2).
3. **Story 2** — crawl4ai MCP extensions. Critical path = T1.1 eTLD+1 → T1.2 dedup (unblock Verifier). Also add T1.6 (Semantic Scholar citation graph client).
4. Before Story 3: full `uv sync` (installs google-adk) — verify ADK on Python 3.13.

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