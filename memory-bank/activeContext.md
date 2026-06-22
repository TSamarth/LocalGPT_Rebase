# Active Context

## Current Phase
**Story 2 (MCP extensions) + Story 3 (all six agents) + Story 4 (checkpoints) — DONE + tested.** Story 2/3 built via parallel git-worktree build (Wave 0 foundation → Wave A 6 tracks → Wave B Acquirer/Verifier, merged to `dev`). Story 4 (CP1/CP2/CP3) built inline 2026-06-22. orchestrator **181** tests green, MCP **43** green, ruff clean, pushed to origin/dev. **Next: Story 5** — research loop wiring (Acquire→Extract→Verify, stop-rule) + live checkpoint registration into the composition root.

## Current Focus
Pipeline now has: shared one-hot model factory (`app/llm.py`, ADK `LiteLlm`→Ollama), deterministic stage machine + orchestrator skeleton (INTAKE→DONE, deep-only CP3 via `ResearchPhase.MID_ACQUIRE`), all six agents (Clarifier/Planner/Acquirer/Extractor/Verifier/Writer), Semantic Scholar citation client (`app/citation.py`), Story 2 MCP extensions (eTLD+1, dedup, seed ingest, PDF routing, rate-limit backoff), and Story 4 checkpoints (`app/checkpoint.py`: `cp1/cp2/cp3_checkpoint` + `Handler` wrappers; `post_handlers`/`phase_handlers` hooks in the orchestrator; `SessionStore.save/load_scored_urls` for CP3's edited source list). All tests run offline (mocked/stubbed model, monkeypatched stdin/editor). **Story 4 nuance**: the checkpoint *mechanism* + handlers landed and are G5-verified by tests that register them directly; registration into the live composition root happens with Story 5 integration (alongside real agent wiring — skeleton still uses stub handlers).

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

## Next Steps (Story 5 + integration)
1. **T4.1 Research loop** — wire Acquire→[CP3]→Extract→Verify inside `Stage.RESEARCH` with the stop-rule (target_evidence + diminishing returns + iteration cap) and budget caps. Replace `_run_research_pass`'s phase-walk stub with real agent calls.
2. **T4.2 Full pipeline integration** — composition root that registers real agents as stage handlers AND registers the checkpoint handlers (`cp1_handler`→`post_handlers[PLAN]`, `cp2_handler`→`post_handlers[WRITE]`, `cp3_handler`→`phase_handlers[MID_ACQUIRE]`). This is where Story 4's mechanism goes live.
3. **T4.3 Adaptive-depth tie-in** — plan depth → loop budgets / target_evidence / cp3_enabled.
4. **Add `mcp` ADK extra** to `orchestrator/pyproject.toml` before the live MCP/Extractor path (currently lazy-imported so offline tests pass).
5. **Run setup probe on real hardware**: `cd orchestrator && python scripts/check_setup.py` — pull Qwen2.5-14B-Q4 (or 8B fallback) + nomic-embed-text; confirm VRAM < 14 GB (Gate G2).

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