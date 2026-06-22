# Active Context

## Current Phase
**Story 2 (MCP extensions) + Story 3 agents (3.1/3.2/3.3/3.5/3.7) — DONE + tested** via parallel git-worktree build (Wave 0 foundation → Wave A 6 parallel tracks, octopus-merged to `dev`, pushed). orchestrator **114** tests green, MCP **43** green, ruff clean. **Wave B deferred**: Acquirer (T3.4) + Verifier (T3.6) — deps merged, both unblocked.

## Current Focus
Pipeline now has: shared one-hot model factory (`app/llm.py`, ADK `LiteLlm`→Ollama), deterministic stage machine + orchestrator skeleton (INTAKE→DONE, deep-only CP3 via `ResearchPhase.MID_ACQUIRE`), 4 agents (Clarifier/Planner/Extractor/Writer), Semantic Scholar citation client (`app/citation.py`), and Story 2 MCP extensions (eTLD+1, dedup, seed ingest, PDF routing, rate-limit backoff). All agent tests run offline (mocked/stubbed model). Next: Wave B (Acquirer + Verifier) wires the research loop's acquire + verify ends.

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

## Next Steps (Wave B + integration)
1. **T3.6 Verifier** (`app/agents/verifier.py`) — deps 2.1+2.2 merged. Independence test (eTLD+1 + cosine < 0.92), 3-tier contradiction confidence, temporal-drift detection (TEMPORAL_DRIFT_THRESHOLD_MONTHS), `ClaimLedger` assembly. Long pole.
2. **T3.4 Acquirer** (`app/agents/acquirer.py`) — deps 2.1+2.3+2.6 merged. MCP discover+triage + citation BFS (academic + deep only, 2-hop, relevance ≥ 0.7 via `citation.py`).
3. **Add `mcp` ADK extra** to `orchestrator/pyproject.toml` before the live MCP/Extractor path (currently lazy-imported so offline tests pass).
4. **Run setup probe on real hardware**: `cd orchestrator && python scripts/check_setup.py` — pull Qwen2.5-14B-Q4 (or 8B fallback) + nomic-embed-text; confirm VRAM < 14 GB (Gate G2).
5. After Wave B: Story 4 (checkpoint CLI + CP1/CP2/CP3 wiring) and Story 5 (research loop + stop-rule + adaptive depth).

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