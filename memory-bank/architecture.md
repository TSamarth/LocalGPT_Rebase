# Architecture: A2A Deep Research Pipeline

Status: DESIGN (proposed). Supersedes "TBD" in [systemPatterns.md]. Built on requirements in [requirements.md].

---

## 0. Framework Decision

**Recommendation: Google ADK + A2A protocol. Keep it.**

Rationale (over LangGraph / CrewAI / custom):
- Existing crawl4ai MCP server is already built and tested against ADK `MCPToolset` (stdio). Switching frameworks throws that integration away.
- ADK has first-class **hierarchical agents** (`LlmAgent` + sub-agents), **A2A protocol** support for agent-to-agent messaging, native **MCP tool** binding, and built-in **session/state + human-in-loop** primitives. Matches every requirement directly.
- ADK orchestration is lightweight Python — the resource cost is the *models*, not the framework. Framework choice is RAM-neutral.
- LangGraph is a strong alternative (graph-explicit control flow) but duplicates what ADK gives free here and drops the MCP/ADK head start. CrewAI is higher-level but weaker on strict sequential resource control and MCP. Custom = reinventing session/checkpoint/transport.

**Binding constraint reminder**: 16 GB RAM (not VRAM) is the limit. Architecture is therefore **sequential single-hot-model**, regardless of how many *logical* agents exist.

---

## 1. Model Strategy (resolves Open Q1)

One hot reasoning model + one hot embedding model. No swap thrash.

| Role | Model | ~VRAM | Notes |
|------|-------|-------|-------|
| Reasoning (all reasoning agents) | Qwen2.5-14B-Instruct Q4_K_M (or Llama-3.1-8B Q5 fallback) | ~9 GB (14B) / ~6 GB (8B) | Shared, role-prompted per agent. `keep_alive=-1` to stay resident. |
| Embedding | nomic-embed-text (existing) | ~0.5 GB | Already used by ChromaStore. |
| KV cache / context (8–16k) | — | ~2–4 GB | Fits remaining VRAM headroom. |

- **VRAM budget**: 14B(9) + embed(0.5) + KV(3) ≈ 12.5 GB of 16 GB. Safe.
- **RAM discipline**: crawl4ai/Playwright (browser) is the RAM hog. **Never run heavy crawl concurrently with large-batch inference.** Stages are sequential, so this is structural, not a runtime guard.
- **One model, many roles**: agents differ by system prompt + tool access, not by model. Avoids load/unload latency. Optional micro-model (Qwen2.5-3B) for cheap classification only if profiling shows the 14B load is a bottleneck — deferred, not in v1.

---

## 2. Agent Hierarchy (A2A topology)

**Pattern: hierarchical coordinator (orchestrator) + single-responsibility specialist agents.** Best-practice A2A: one root delegates; specialists are stateless, communicate via structured artifacts, never share raw context.

```
                    ┌─────────────────────────┐
                    │   Orchestrator (root)    │  owns session, stage machine,
                    │   LlmAgent + state       │  checkpoints, budget, stop-rule
                    └────────────┬────────────┘
        delegates (A2A) ─────────┼──────────────────────────────
        │           │            │            │          │       │
   ┌────▼───┐  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐ ┌───▼────┐ ┌▼────────┐
   │Clarifier│ │ Planner │  │Acquirer │  │Extractor│ │Verifier│ │ Writer  │
   │ agent   │ │ agent   │  │ agent   │  │ agent   │ │ agent  │ │ agent   │
   └─────────┘ └─────────┘  └────┬────┘  └────┬────┘ └────────┘ └─────────┘
                                 │ MCP        │ MCP
                                 ▼            ▼
                    ┌───────────────────────────────────┐
                    │   crawl4ai MCP server (existing)   │
                    │  discover_urls · score_and_triage  │
                    │  crawl_url/many · deep/adaptive     │
                    │  search_chunks · get_crawl_stats    │
                    │  SQLite + ChromaDB + Ollama embed   │
                    └───────────────────────────────────┘
```

**Least-privilege tool access**: only **Acquirer** and **Extractor** hold the MCP toolset. Clarifier/Planner/Verifier/Writer are reasoning-only (operate on artifacts + `search_chunks` read where needed). Shrinks each agent's tool surface → cheaper prompts, fewer mistakes.

---

## 3. Agent Roles & Contracts

Each agent: single responsibility, typed input artifact → typed output artifact. (Schemas = design contracts, not implementation.)

### 3.1 Orchestrator (root `LlmAgent` + deterministic stage machine)
- Owns: session id, stage state, evidence budget, stop-rule, two checkpoints.
- Delegates to specialists in order; loops Acquire→Extract→Verify until stop-rule.
- Does NOT crawl or reason about content — pure control.

### 3.2 Clarifier (FR1)
- In: raw user query.
- Logic: detect (a) ambiguous scope, (b) missing constraints (timeframe/region/version/audience/...). 
- Out: `{status: "clear" | "needs_input", questions: [...], normalized_query}`.
- If `needs_input` → orchestrator surfaces questions to user **before run start only**.

### 3.3 Planner (FR2)
- In: normalized query.
- Out: `ResearchPlan`:
```
ResearchPlan {
  subtopics: [{ id, question, target_evidence: int, source_classes: [web|academic|code|seed] }],
  depth: shallow|normal|deep,        # adaptive to query complexity
  seed_urls: [url],                   # user-provided (FR3.2)
  est_iterations: int
}
```
- `target_evidence` per subtopic scales with depth → drives stop-rule (Open Q5).
- → **Checkpoint 1** (orchestrator presents plan).

### 3.4 Acquirer (FR3.1, FR3.4, FR3.5)
- In: subtopic question(s) + source_classes.
- Tools: `discover_urls` (SerpAPI+DDG+arXiv), `score_and_triage_urls`, Semantic Scholar citation graph API (T1.6 utility, not MCP tool).
- **Citation BFS** (`source_class == academic` AND `plan.depth == "deep"` only): after initial discovery, for each academic result fetch citation graph via Semantic Scholar API. BFS queue, 2-hop depth limit. Relevance gate: LLM (Qwen2.5-14B Q4) ranks candidate papers against current subtopic question; only enqueue if relevance ≥ threshold. Prevents unbounded traversal. Enriches `ScoredURL` with `publication_date` and `citation_refs`.
- Out: `[ScoredURL {url, score, strategy, source, also_in, etld1, publication_date, citation_refs}]` (triaged, deduped, date-enriched).

### 3.5 Extractor (FR3.3, FR5.6)
- In: triaged `ScoredURL[]`.
- Tools: `crawl_url` / `crawl_many` / `deep_crawl` / `adaptive_crawl` (per triage strategy), persisted to SQLite+Chroma.
- **Publication date propagation**: if `ScoredURL.publication_date` is set (from Semantic Scholar/arXiv metadata via Acquirer), write it to chunk metadata in Chroma. Verifier reads this for temporal drift detection. Zero extra inference — metadata passthrough only.
- Out: confirmation + `page_ids`; content + date metadata live in stores (handoff is IDs, not text — RAM/token discipline).

### 3.6 Verifier (FR5, FR5.5, FR5.6) — the trust core
- In: subtopic, `search_chunks(subtopic)` retrieval (includes chunk `publication_date` metadata).
- Logic:
  - Extract candidate claims from retrieved chunks.
  - **Independent-source test** (Open Q2): two sources count as independent iff different **eTLD+1** AND content cosine-similarity < 0.92 (catches syndication/mirrors). MCP already tracks canonical URL + `also_in`.
  - Claim corroborated by ≥2 independent sources → **keep** (FR5.1).
  - Claim contradicted across independent sources → **flag**, retain all sides (FR5.2).
  - Single-source → mark `uncorroborated` (AC5).
  - **Contradiction confidence scoring** (FR5.5): for each flagged contradiction, score using three-tier rubric: (1) source independence (same eTLD+1 = low weight), (2) recency delta (publication date gap between conflicting sources), (3) methodological explicitness (source cites data/methods = higher weight). Emit `confidence_score: float` + `conflict_type`.
  - **Temporal drift detection** (FR5.6): if two contradicting sources have `publication_date` delta ≥ `temporal_drift_threshold` (default 18 months, configurable), classify as `conflict_type: "temporal_drift"` instead of `"factual"`. Tag parent claim `temporal_status: "dated"` (older source) or `"current"` (newer). Temporal drift is surfaced in contradictions appendix with its own label — not silently resolved.
- Out: `ClaimLedger`:
```
ClaimLedger {
  claims: [{ id, subtopic_id, text,
             status: kept|flagged|uncorroborated,
             temporal_status: current|dated|uncertain|null,
             sources: [{url, etld1, quote, publication_date}],
             contradictions: [{url, quote,
                               confidence_score: float,
                               conflict_type: temporal_drift|methodological|factual}] }]
}
```

### 3.7 Writer (FR6)
- In: `ClaimLedger` + `ResearchPlan` (for coverage check).
- Out: structured markdown — exec summary → section per subtopic → sources → contradictions appendix. Every kept claim cites source URL(s) (FR5.3).
- Coverage check (FR6.3): every plan subtopic appears; gaps noted explicitly.
- → **Checkpoint 2** (orchestrator presents draft) → final write.

---

## 4. Orchestration: Stage Machine + Research Loop

```
INTAKE → CLARIFY ──needs_input──> [ask user] ──┐
   │                                            │ (before run only)
   └──clear──────────────────────────────<──────┘
        ▼
     PLAN  ──> ★ CHECKPOINT 1 (approve/edit plan) ──reject──> re-plan
        ▼ approve
   ┌─ RESEARCH LOOP (per subtopic, budget-bounded) ──────────────────────────────┐
   │   ACQUIRE → [★ CP3 if depth=deep] → EXTRACT → VERIFY                        │
   │   CP3 = mid-acquisition checkpoint: show source list + initial theme         │
   │         clusters; user may add search terms, exclude URLs, redirect topic.   │
   │         Edits trigger a supplemental Acquirer pass before Extractor runs.    │
   │   stop subtopic when: target_evidence met                                    │
   │                   OR diminishing returns (<N new claims)                     │
   │                   OR iteration budget hit                                    │
   └─ loop until all subtopics satisfied ───────────────────────────────────────┘
        ▼
     SYNTHESIZE (draft) ──> ★ CHECKPOINT 2 (approve/edit) ──reject──> revise
        ▼ approve
     WRITE final markdown  →  data/reports/{session}.md
```

**Stop-rule (resolves Open Q5)**: subtopic done when corroborated-claim count ≥ `target_evidence`, OR an Acquire→Extract→Verify pass adds fewer than `N_min` new unique claims (diminishing returns), OR per-subtopic iteration cap reached. Whole run done when all subtopics done. Caps prevent runaway compute.

---

## 5. State & Persistence (resolves Open Q4)

- **ADK session state**: live stage, current subtopic, budgets.
- **Existing SQLite** (`crawl_sessions`, `crawled_pages`, `chunks`): all crawled content + provenance. Reuse as-is.
- **Session artifact dir** `data/sessions/{session_id}/`: `plan.json`, `claim_ledger.json`, `draft.md`, `stage.json`.
- **Resume**: on restart, load `stage.json` → skip completed stages, re-enter loop where it stopped. Crawled content already in stores → no re-crawl.

---

## 6. Checkpoint UX (resolves Open Q6)

Local tool → **CLI-first, file-fallback**:
- Checkpoint prints summary to terminal; prompts `[a]pprove / [e]dit / [r]eject`.
- `edit` opens the artifact (`plan.json` / `draft.md`) in `$EDITOR`; on save, re-validate and continue.
- Blocking (AC2): loop halts until user acts. Implemented as ADK human-in-loop interrupt.

**Checkpoint 3 (CP3) — mid-acquisition, deep plans only**:
- Fires: after Acquirer completes URL discovery + citation BFS, before Extractor crawls.
- Guard: `plan.depth == "deep"` only. Shallow/normal plans skip CP3 entirely.
- Shows: ranked source list with scores, initial theme clusters derived from titles/abstracts, coverage estimate per subtopic.
- Prompts: `[a]pprove / [+]add search terms / [-]exclude URLs / [r]redirect subtopic`.
- On `[+]` or `[r]`: Acquirer runs a supplemental discovery pass (bounded), re-presents updated list.
- On `[a]pprove`: Extractor proceeds.
- Same ADK human-in-loop interrupt mechanism as CP1/CP2.

---

## 7. crawl4ai MCP Extensions (resolves Open Q3)

Targeted additions to existing server (extend, don't rebuild — FR3.4):
1. **Content-similarity dedup**: collapse syndicated/mirrored pages (embedding cosine ≥ 0.92) so Verifier's independence test is reliable. (New: dedup pass in `discover`/persist.)
2. **eTLD+1 field**: ensure `discover_urls` output + chunk metadata carry registrable domain for independence test. (Likely partial via `also_in`/canonical — verify, fill gap.)
3. **Seed-URL ingest path**: tool/param to force user-provided URLs/files into the crawl set (FR3.2, US5).
4. **PDF extraction quality**: validate arXiv/PDF → markdown fidelity; improve if lossy.
5. **Rate-limit/backoff**: per-domain politeness + retry on 429 (robustness for deep runs).
6. **Semantic Scholar citation graph API client** (T1.6 — utility, not a new MCP tool): callable by Acquirer agent. Given a paper ID (arXiv or S2 ID), fetches `publication_date`, `references[]`, `citers[]`. Used for citation BFS (§11) and date enrichment (§12). Semantic Scholar Graph API v1 — free, no auth for read-only access at normal research volumes. Implemented in `orchestrator/` as an `httpx` utility, not inside the MCP server.

---

## 8. Data Flow (end-to-end)

```
user query
  → Clarifier (maybe ask user)
  → Planner → ResearchPlan ──★CP1
  → loop (per subtopic):
      Acquirer: discover_urls + score_and_triage_urls → ScoredURL[]
               [if academic + deep: citation BFS via Semantic Scholar API, 2-hop]
               [ScoredURL enriched with publication_date, citation_refs]
      [★CP3 if depth=deep: show sources + themes → user may steer]
      Extractor: crawl_* → SQLite + ChromaDB (content + provenance + publication_date)
      Verifier: search_chunks → claims → independence test
               → contradiction confidence scoring (three-tier rubric)
               → temporal drift detection (date delta ≥ 18mo → temporal_drift)
               → ClaimLedger (with temporal_status, confidence_score, conflict_type)
  → Writer: ClaimLedger + Plan → draft.md ──★CP2
  → final markdown report  (sourced claims, confidence-ranked contradictions appendix,
                             temporal drift section)
```

---

## 9. Best-Practice A2A Checklist (applied)
- ✅ Single responsibility per agent.
- ✅ Hierarchical coordinator (orchestrator delegates; specialists don't call each other directly).
- ✅ Typed artifact contracts between agents; **handoffs pass IDs/structured data, not raw text** (RAM + token discipline).
- ✅ Least-privilege tool access (only Acquirer/Extractor touch MCP write tools).
- ✅ Stateless specialists; state centralized in orchestrator/session store.
- ✅ Deterministic control flow (stage machine) wrapping LLM reasoning — LLMs decide *content*, not *control*.
- ✅ Resource-aware: one hot model, sequential stages, bounded crawl concurrency.
- ✅ Human-in-loop as explicit blocking gates.

---

## 10. Component Inventory (build targets)
| Component | New/Existing | Notes |
|-----------|--------------|-------|
| Orchestrator + stage machine | New | ADK root agent + deterministic loop; MID_ACQUIRE stage for CP3 |
| Clarifier / Planner / Writer agents | New | reasoning-only LlmAgents |
| Acquirer agent | New | MCP tools + Semantic Scholar citation BFS (deep plans) |
| Extractor agent | New | MCP crawl tools + publication_date passthrough |
| Verifier agent | New | independence test + contradiction confidence + temporal drift |
| Artifact schemas (Plan, ScoredURL, ClaimLedger) | New (Story 1 done) | Pydantic v2; schema extension needed pre-Story-2 |
| Session artifact store + resume | New (Story 1 done) | JSON dir + ADK state |
| crawl4ai MCP (5 extensions §7) | Extend existing | dedup, eTLD+1, seed, PDF, backoff |
| Semantic Scholar citation API client (T1.6) | New | `orchestrator/app/citation.py`, httpx utility |
| SQLite/Chroma/Ollama stores | Existing | reuse |

## 11. Citation Network Traversal Design (P1)

**Trigger**: `source_class == academic` AND `plan.depth == "deep"`. Not active for shallow/normal plans.

**Algorithm**:
```
for each academic ScoredURL in initial discovery:
    paper_id = extract_paper_id(url)   # arXiv ID or S2 paper ID
    if paper_id:
        hop1 = semantic_scholar_api(paper_id).references  # immediate references
        for candidate in hop1:
            relevance = llm_score(candidate.title + candidate.abstract, subtopic_question)
            if relevance >= CITATION_RELEVANCE_THRESHOLD:
                enqueue(candidate, depth=2)
        hop2 = [semantic_scholar_api(c).references for c in hop1_enqueued]
        # hop2 candidates also relevance-gated before enqueue
```

**Bounds**: max 2 hops. `CITATION_RELEVANCE_THRESHOLD` configurable (default 0.7 on 0–1 scale). Per-subtopic BFS budget cap in config. BFS queue is bounded; no unbounded recursion.

**Resource note**: LLM scoring uses already-resident Qwen2.5-14B Q4 (role-prompted). No model swap. API calls are lightweight JSON — negligible RAM.

**Output**: enriched `ScoredURL[]` with `publication_date` (from S2 metadata) and `citation_refs` (IDs of cited papers that were followed). Feeds directly into normal Extractor crawl pipeline.

---

## 12. Enhanced Verification Design (P2 + P4)

### 12.1 Contradiction Confidence Scoring (P2)

Each `contradiction` record in `ClaimLedger` carries:
- `confidence_score: float` (0.0–1.0): how confident we are this is a real contradiction
- `conflict_type: "factual" | "methodological" | "temporal_drift"`

**Three-tier scoring rubric** (additive, each tier contributes 0–0.33):
1. **Source independence** (0–0.33): sources from different eTLD+1 + low cosine similarity → higher score. Same org/domain = 0.
2. **Recency delta** (0–0.33): both sources recent (< 12 months) → higher weight on contradiction; large date gap → signals temporal drift rather than factual conflict.
3. **Methodological explicitness** (0–0.33): source cites specific data/methodology vs. assertion-only → higher weight. LLM evaluates from chunk text.

Output sorted by `confidence_score` descending in contradictions appendix.

### 12.2 Temporal Drift Detection (P4)

**Data flow**: `publication_date` flows: Semantic Scholar API → `ScoredURL.publication_date` → Extractor chunk metadata → Verifier `search_chunks` result → claim comparison.

**Logic**:
```
if contradiction detected:
    date_delta = abs(source_A.publication_date - source_B.publication_date)
    if date_delta >= config.temporal_drift_threshold (default: 18 months):
        conflict_type = "temporal_drift"
        older_source.claim → temporal_status = "dated"
        newer_source.claim → temporal_status = "current"
    else:
        conflict_type = "factual" | "methodological"
```

**Report output**: contradictions appendix has a "Temporal Drift" sub-section separate from "Factual Contradictions". Temporal drift is informational, not alarming — "field has evolved since [date]" framing. Acceptance criterion AC7: planted temporal drift case appears in temporal drift section, not factual contradictions.

**Fallback**: if `publication_date` is null for one or both sources, skip temporal classification → `temporal_status = "uncertain"`.

---

**Next step**: `/sc:workflow` to update task breakdown, or `/sc:implement` to execute (start: schema extension pre-Story-2).
| Checkpoint CLI | New | blocking human-in-loop |
| crawl4ai MCP (5 extensions §7) | Extend existing | dedup, eTLD+1, seed ingest, PDF, backoff |
| SQLite/Chroma/Ollama stores | Existing | reuse |

---

**Next step**: `/sc:workflow` to sequence implementation, or `/sc:implement` to build a component (start: artifact schemas + orchestrator skeleton, or the MCP §7 extensions).