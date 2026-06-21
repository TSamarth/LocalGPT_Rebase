# Story 2 + Story 3 — Parallel Implementation Plan

## Context

Story 1 (schemas, config, session store, ADK scaffold) is **DONE** — Gate G1 cleared,
schemas frozen, 19 tests green, T0.4 enrichment fields already present. The next two
stories build the actual pipeline:

- **Story 2** — crawl4ai MCP extensions (dedup, provenance, robustness) + Semantic Scholar client.
- **Story 3** — the agent layer: orchestrator stage machine + 6 specialist agents.

The user wants these implemented **in parallel via subagents with zero file conflicts**.
The blocker to naive parallelism is *shared mutable surfaces* (`config.py` in both packages,
a new shared ADK model factory) and *intra-Story-2 file overlap* (`crawl.py`, `discover.py`,
`storage/*` touched by multiple tasks). This plan isolates work into **git worktrees** with
**disjoint new-file ownership**, after a serial Wave 0 that pre-lands every shared surface so
branches never edit the same file.

**Decisions (user-confirmed):** git worktree per track · unblocked tasks only (defer 3.4 Acquirer,
3.6 Verifier) · spawn subagents after plan approval.

---

## Dependency facts (from `claudedocs/spawn_a2a_research.md`)

- Critical path: `2.1 eTLD+1 → 2.2 dedup → 3.6 Verifier`.
- `3.4 Acquirer` needs `2.1 + 2.3 + 2.6`. `3.6 Verifier` needs `2.1 + 2.2`. → **both deferred to Wave B**.
- Independent now: `2.3`, `2.4→2.5`, `2.6`, agents `3.2 / 3.3 / 3.5 / 3.7`.
- All schema fields needed (publication_date, citation_refs, temporal_status, confidence_score,
  conflict_type) **already exist** in `orchestrator/app/schemas.py` — frozen, read-only, no conflict.

## File-ownership / conflict map

| File | Touched by | Resolution |
|------|-----------|------------|
| `mcp/.../tools/crawl.py` | 2.4 (PDF), 2.5 (rate-limit) | same track, sequential |
| `mcp/.../tools/discover.py`, `storage/*` | 2.1, 2.2 | same track, 2.1 then 2.2 |
| `mcp/.../common.py` | 2.3 (registers new seed tool) | MCP track only |
| `orchestrator/app/citation.py` | 2.6 | new file, isolated track |
| `orchestrator/app/agents/*.py` | 3.2/3.3/3.5/3.7 | one new file each, disjoint |
| `orchestrator/app/llm.py` (NEW shared) | every agent | **Wave 0** — pre-built, read-only after |
| `orchestrator/app/config.py` | citation + agents | **Wave 0** — all keys added upfront |
| `mcp/.../app/config.py` | 2.5, 2.3 | **Wave 0** — keys added upfront |

---

## Wave 0 — Foundation (SERIAL, on `dev`, main thread; commit before branching)

This must be a single committed base so all worktrees branch from it and never re-edit it.

1. **`orchestrator/app/config.py`** — add (all read-only thereafter):
   - Semantic Scholar: `S2_API_BASE_URL`, `S2_RATE_DELAY_SEC` (1.0), `CITATION_BFS_HOPS` (2),
     `CITATION_RELEVANCE_THRESHOLD` (0.7).
   - Any ADK/model knobs agents need beyond existing `REASONING_MODEL` / `OLLAMA_BASE_URL`.
2. **`mcp/Crawl4AI_MCP/app/config.py`** — add per-domain rate-limit + backoff keys (for 2.5),
   seed-ingest keys if any (for 2.3), using the existing `_env*` helpers.
3. **`orchestrator/app/llm.py`** (NEW) — the shared one-hot model factory: builds an ADK
   `LlmAgent` bound to Ollama Qwen2.5-14B via `REASONING_MODEL`, with a role-prompt helper.
   **This is the ADK A2A spike — de-risk here.** Every agent imports this; it must be stable first.
4. **`orchestrator/app/agents/__init__.py`** (NEW, empty package marker; agents imported directly).
5. **3.1 skeleton** — `orchestrator/app/orchestrator.py` + `orchestrator/app/stage_machine.py`:
   deterministic stage machine `INTAKE→CLARIFY→PLAN→RESEARCH(+MID_ACQUIRE for CP3)→SYNTHESIZE→WRITE→DONE`,
   resume via `SessionStore.load_stage()`. Agents wired as stubs (import-tolerant) so the skeleton
   runs before agents land.
6. Run `cd orchestrator && uv run pytest` (existing 19 green) → **commit on `dev`**.

Reuse: `SessionStore` (`app/session.py`), `config` singleton, `Stage` enum + all artifact models
in `app/schemas.py`. Do **not** edit `schemas.py`.

---

## Wave A — Parallel worktree tracks (spawn after Wave 0 commit)

Each track = one `isolation: worktree` subagent branched from the Wave 0 commit. Each delivers
**code + its own tests green**. Files are disjoint or were pre-staged in Wave 0 (read-only here).

| Track / branch | Tasks | Owns (writes) | Reads (no edit) |
|----------------|-------|---------------|-----------------|
| `feat/s2-mcp` | 2.1→2.2, 2.3, 2.4→2.5, 2.7 | `tools/discover.py`, `tools/crawl.py`, new `tools/seed.py`, `storage/*`, `common.py`, MCP `test_*.py` | MCP `config.py` |
| `feat/s2-citation` | 2.6, 2.7(client) | NEW `orchestrator/app/citation.py`, `orchestrator/tests/test_citation.py` | orch `config.py`, `schemas.py` |
| `feat/s3-clarifier` | 3.2, 3.8(unit) | NEW `app/agents/clarifier.py`, `tests/test_clarifier.py` | `app/llm.py`, `schemas.py`, `config.py` |
| `feat/s3-planner` | 3.3, 3.8(unit) | NEW `app/agents/planner.py`, `tests/test_planner.py` | `app/llm.py`, `schemas.py`, `config.py` |
| `feat/s3-extractor` | 3.5, 3.8(unit) | NEW `app/agents/extractor.py`, `tests/test_extractor.py` | `app/llm.py`, `schemas.py`, `config.py` |
| `feat/s3-writer` | 3.7, 3.8(unit) | NEW `app/agents/writer.py`, `tests/test_writer.py` | `app/llm.py`, `schemas.py`, `config.py` |

Per-track acceptance (from spawn doc):
- **2.1** eTLD+1 in every discovered URL + persisted to chunk metadata (SQLite + Chroma).
- **2.2** cosine ≥ 0.92 collapses mirror set to canonical (verified on a labelled set).
- **2.3** user seed URLs/files forced into crawl set (new tool registered in `common.py`).
- **2.4** arXiv/PDF→markdown fidelity validated + lossy cases fixed.
- **2.5** per-domain rate-limit enforced; 429 → backoff retry.
- **2.6** `get_paper_metadata(id) → {publication_date, references, citers}`; arXiv/S2 id parsing; 1 s courtesy delay; mocked-response tests.
- **3.2** returns `ClarifyResult`; ambiguity + missing-constraint detection.
- **3.3** returns `ResearchPlan`; depth varies by complexity; `target_evidence` scales via `TARGET_EVIDENCE_*`.
- **3.5** crawls `ScoredURL[]` via MCP; passthrough `publication_date` → chunk metadata (no inference).
- **3.7** `ClaimLedger`+`ResearchPlan` → markdown; coverage check (every subtopic); temporal-drift sub-section separate from factual contradictions.

Merge: branches back to `dev` in any order — disjoint new files + Wave-0-staged shared files mean
clean merges. Run full suite after each merge.

---

## Wave B — Deferred (after Wave A merged; not in this batch)

- **3.4 Acquirer** (`app/agents/acquirer.py`) — needs 2.1 + 2.3 + 2.6 merged. MCP discover+triage + citation BFS (academic+deep only, 2-hop, relevance ≥ 0.7).
- **3.6 Verifier** (`app/agents/verifier.py`) — needs 2.1 + 2.2 merged. Independence test (eTLD+1 + cosine), 3-tier contradiction confidence, temporal-drift detection, `ClaimLedger` assembly. **Long pole.**

---

## Verification

- Per track (inside its worktree): `cd orchestrator && uv run pytest tests/<file> -v`
  or `cd mcp/Crawl4AI_MCP && uv run pytest tests/ -v` — track-local green before merge.
- After all merges to `dev`:
  - `cd orchestrator && uv run pytest -v` (19 existing + new agent/citation tests).
  - `cd mcp/Crawl4AI_MCP && uv run pytest -v` (offline MCP suite + extension tests).
  - `cd orchestrator && uv run ruff check app/ tests/`.
- ADK spike smoke: run the 3.1 skeleton against a stubbed/mock model to confirm stage transitions + resume.
- Gate checks: **G3** = dedup collapses mirrors + eTLD+1 populated + seeds ingested + S2 client returns metadata. **G4** = each agent isolated-green with mocked model.

## Execution after approval

1. Do Wave 0 on `dev`, run tests, commit.
2. Spawn the 6 Wave A worktree subagents (parallel), each with its task brief above.
3. As each returns green, merge its branch to `dev`; rerun full suite.
4. Report status; Wave B (3.4, 3.6) is a separate follow-up batch.
