# Architecture: A2A Deep Research Pipeline

Status: **DESIGN v2 (ratified 2026-06-23)** — commits project to **ADK 2.x**, **ADK-owned sessions**, **dynamic-workflow** orchestration, **single local-first A2A boundary**. Supersedes v1 hand-rolled-orchestrator design (Stories 1–5); §15 = migration path. Section numbers ≤ §12 preserved so existing `§`-references stay valid; §13–§15 new. Input review: [../claudedocs/adk_alignment_review.md]. Requirements: [requirements.md].

---

## 0. Framework Decision (ratified)

**Google ADK 2.x + A2A protocol. Committed.**

| Decision | Choice | Why |
|----------|--------|-----|
| Framework + major | **Google ADK, pinned `>=2.3,<3`** | Already running ADK 2.3.0 (uv.lock); 2.0 GA'd 2026-05-19. Loose `>=0.3.0` floor spanned 1.x→2.0 break — must pin (review §0.2). |
| Orchestration substrate | **ADK 2.0 dynamic workflows** (`@node` / `Workflow` / `ctx.run_node`) | Near-1:1 fit for hand-rolled stage machine + research loop; gives automatic checkpoint-resume + native ADK sessions; retires `orchestrator.py` / `stage_machine.py` / `_run_sync` (review §2). |
| Session & resume ownership | **ADK session + event store** (`ResumabilityConfig(is_resumable=True)`) | ADK persists step/event state; resume re-runs only unfinished nodes. `data/sessions/{id}/*.json` demoted to **write-through export**, not source of truth (§5). |
| Inter-agent topology | **Local sub-agents / workflow nodes** on one hot model | Six specialists share one resident model, run sequentially (16 GB RAM). ADK's A2A guidance: in-process, shared-model, performance-coupled agents should be *local sub-agents*, **not** A2A peers — A2A network+serialization overhead buys nothing here. |
| A2A boundary | **One A2A server at pipeline edge, local-first** | Whole Deep-Research pipeline exposed as single `A2AServer` (auto agent card), consumable over **localhost** by local client or another ADK agent via `RemoteA2aAgent`. Real A2A contract at edge; no internal A2A overhead (§13). |
| Runtime / front-end | **`adk api_server` (local service)** + thin CLI | A2A exposure, `RequestInput` HITL over API/SSE, resume-by-`invocation_id` all work natively. Single box, local Ollama — no cloud. |
| Tool transport | **MCP (unchanged)** | crawl4ai stays stdio `MCPToolset`. MCP = right boundary for tools; A2A = right boundary for agent. |

**Binding constraint (unchanged):** 16 GB **RAM** (not VRAM) = limit ⇒ **sequential, single-hot-model** execution regardless of logical node count. Why six are local nodes, not A2A microservices.

---

## 1. Model Strategy (resolves Open Q1)

One hot reasoning model + one hot embedding model. No swap thrash.

| Role | Model | ~VRAM | Notes |
|------|-------|-------|-------|
| Reasoning (all reasoning nodes) | Qwen2.5-14B-Instruct Q4_K_M (Llama-3.1-8B Q5 fallback) | ~9 GB / ~6 GB | Shared, role-prompted per node. `keep_alive=-1` resident. |
| Embedding | nomic-embed-text | ~0.5 GB | Used by crawl4ai ChromaStore. |
| KV cache / context (8–16k) | — | ~2–4 GB | Fits remaining headroom. |

- **VRAM budget**: 14B(9) + embed(0.5) + KV(3) ≈ 12.5 GB of 16 GB. Safe.
- **RAM discipline**: crawl4ai/Playwright = RAM hog. Never run heavy crawl concurrently with large-batch inference — structural; nodes run sequentially.
- **One model, many roles**: nodes differ by system prompt + tool access, not model. Via ADK `LiteLlm` → local Ollama with `ollama_chat/` provider prefix.
- **Connectivity (review §0.1):** set **`OLLAMA_API_BASE` env var** (not only `api_base` param) at startup — LiteLLM routes non-generation calls through env var. Without it, non-default `OLLAMA_BASE_URL` silently hits localhost.

---

## 2. Agent Topology: Local Nodes Under One A2A Boundary

**Pattern: dynamic-workflow root composes single-responsibility specialist nodes; whole pipeline = one A2A-exposed agent.** Specialists = local nodes (in-process, one hot model), never A2A peers, never calling each other directly — data flows through workflow.

```
        localhost A2A client  /  another ADK agent (RemoteA2aAgent)
                          │  A2A protocol (localhost, well-known agent card)
                          ▼
        ┌─────────────────────────────────────────────────────────┐
        │  A2AServer  ──  Deep-Research Agent  (adk api_server --a2a)│
        │  ┌───────────────────────────────────────────────────┐   │
        │  │  Workflow (root)  —  @node research(ctx, query)    │   │
        │  │    owns control flow + stop-rule; ADK owns session │   │
        │  │   Clarifier → Planner →★CP1                        │   │
        │  │     → [ Acquirer →★CP3? → Extractor → Verifier ]*  │   │
        │  │     → Writer →★CP2                                 │   │
        │  └───────────────────────────────────────────────────┘   │
        │            local sub-agents · ONE hot model · sequential  │
        └───────────────────────────────┬─────────────────────────┘
                          Acquirer/Extractor/Verifier │ MCP (stdio)
                                                       ▼
                            ┌───────────────────────────────────┐
                            │   crawl4ai MCP server (existing)   │
                            │  discover_urls · score_and_triage  │
                            │  crawl_url/many · deep/adaptive     │
                            │  search_chunks · get_crawl_stats    │
                            │  SQLite + ChromaDB + Ollama embed   │
                            └───────────────────────────────────┘
```

**Least-privilege tool access (unchanged):** only **Acquirer** (read: `discover_urls`, `score_and_triage_urls`) and **Extractor** (write: `crawl_*`) and **Verifier** (read: `search_chunks`) hold MCP tools; Clarifier/Planner/Writer = reasoning-only. `MCPToolset` for each built **once per run and reused** across loop passes, then `close()`d at run end (review §0.3) — no per-pass subprocess churn.

---

## 3. Agent Roles & Contracts

Each specialist: single responsibility, typed input artifact → typed output artifact. Schemas (`orchestrator/app/schemas.py`, Pydantic v2) = **inter-node contracts**, unchanged by v2 design — only *call mechanism* changes (node invoked via `await ctx.run_node(agent, input)`, returns output directly; no throwaway `Runner`/session extraction).

### 3.1 Root workflow (`@node` dynamic workflow, replaces v1 orchestrator agent)
- Owns: control flow (stage order + research loop), stop-rule, checkpoint placement.
- Does **not** own session/resume persistence — ADK does (§5).
- Does **not** crawl or reason about content — pure control. Deterministic Python; LLMs decide content, not control.

### 3.2 Clarifier (FR1)
- In: raw user query.
- Logic: detect (a) ambiguous scope, (b) missing constraints (timeframe/region/version/audience/…).
- Out: `ClarifyResult{status: "clear" | "needs_input", questions: [...], normalized_query}`.
- `needs_input` → surfaced to user **before run start only** (pre-flight `RequestInput`, §6).

### 3.3 Planner (FR2)
- In: normalized query.
- Out: `ResearchPlan{ subtopics:[{id, question, target_evidence:int, source_classes:[web|academic|code|seed]}], depth: shallow|normal|deep, seed_urls:[url], est_iterations:int }`.
- `target_evidence` per subtopic = deterministic post-step scaled by `depth` (config `TARGET_EVIDENCE_*`) → drives stop-rule.
- → **Checkpoint 1**.

### 3.4 Acquirer (FR3.1, FR3.4, FR3.5)
- In: subtopic question(s) + source_classes.
- Tools: `discover_urls` (SerpAPI+DDG+arXiv), `score_and_triage_urls`; Semantic Scholar citation-graph API (utility in `app/citation.py`, **not** MCP tool).
- **Citation BFS** (`source_class == academic` AND `plan.depth == "deep"` only): 2-hop BFS over S2 citation graph, LLM relevance-gated per candidate; enriches `ScoredURL` with `publication_date` + `citation_refs` (§11).
- Out: `[ScoredURL{url, score, strategy, source, also_in, etld1, publication_date, citation_refs}]` (triaged, deduped, date-enriched).

### 3.5 Extractor (FR3.3, FR5.6)
- In: triaged `ScoredURL[]`.
- Tools: `crawl_url` / `crawl_many` / `deep_crawl` / `adaptive_crawl` (per triage strategy) → SQLite + Chroma.
- **Publication-date passthrough**: copies `ScoredURL.publication_date` into chunk metadata (no inference). Verifier reads for temporal-drift.
- Out: page IDs; content + provenance + date live in stores (handoff = IDs, not text).

### 3.6 Verifier (FR5, FR5.5, FR5.6) — the trust core
- In: subtopic; retrieves own chunks via `search_chunks` (includes chunk `publication_date`).
- **Model owns content** (which chunks support/contradict; 0–1 methodological-explicitness signal). **Deterministic Python owns policy** — re-applied to every parsed ledger:
  - **Independence** (Open Q2): different **eTLD+1** AND content cosine < `INDEPENDENCE_COSINE_THRESHOLD` (0.92).
  - **Claim status**: ≥2 independent → **kept**; independent contradiction → **flagged** (retain all sides); single → **uncorroborated**.
  - **Contradiction confidence** (FR5.5): three-tier additive rubric (§12.1) → `confidence_score`.
  - **Temporal drift** (FR5.6): date gap ≥ `TEMPORAL_DRIFT_THRESHOLD_MONTHS` (18) → `conflict_type="temporal_drift"` + `temporal_status` (§12.2).
- Out: `ClaimLedger` (claims with status, temporal_status, sources, contradictions{confidence_score, conflict_type}).

### 3.7 Writer (FR6)
- In: `ClaimLedger` + `ResearchPlan`.
- Out: structured markdown — exec summary → section per subtopic → sources → contradictions appendix (factual/methodological vs. separate **Temporal Drift** sub-section). Every kept claim cites source URL(s).
- Deterministic coverage check (FR6.3): every plan subtopic appears; gaps surfaced explicitly (model-free skeleton).
- → **Checkpoint 2** → final write.

---

## 4. Orchestration: Dynamic Workflow + Research Loop

Control flow = single ADK **dynamic workflow** (`@node`), not hand-rolled driver. Nodes invoked with `await ctx.run_node(...)`, return value *is* node output — eliminates v1 `asyncio.run` bridge and manual `output_key`-from-discarded-session extraction.

```python
# sketch — orchestrator/app/workflow.py (v2 target)
@node(rerun_on_resume=True)
async def research(ctx: Context, raw_query: str) -> str:
    clarified = await ctx.run_node(clarifier_agent, raw_query)
    if clarified.status == "needs_input":
        answers = yield RequestInput(message=..., response_schema=...)   # pre-flight only
        clarified = await ctx.run_node(clarifier_agent, merge(raw_query, answers))

    plan = await ctx.run_node(planner_agent, clarified.normalized_query)
    plan = yield RequestInput(message="Approve/edit plan", payload=plan,
                              response_schema=ResearchPlan)              # ★ CP1

    for st in plan.subtopics:                       # sequential (RAM); fan-out is a future option (§14)
        iteration, new_claims = 0, None
        while not stop_rule(ledger, st, iteration=iteration, new_claims=new_claims,
                            budget=depth_budget(plan.depth)):
            urls = await ctx.run_node(acquirer_node, (st, plan))
            if plan.depth == "deep":
                urls = yield RequestInput(message="Review sources", payload=urls,
                                          response_schema=ScoredURLList)  # ★ CP3 (deep only)
            await ctx.run_node(extractor_node, urls)
            new = await ctx.run_node(verifier_node, st.question)
            ledger = merge_ledger(ledger, new); iteration += 1

    coverage_report(plan, ledger)                   # deterministic, model-free
    draft = await ctx.run_node(writer_agent, (plan, ledger))
    draft = yield RequestInput(message="Approve/edit draft", payload=draft,
                               response_schema=str)                       # ★ CP2
    return draft

root_agent = Workflow(name="deep_research", edges=[("START", research)])
```

**Stage identity preserved.** Canonical stage names (`INTAKE → CLARIFY → PLAN → RESEARCH(ACQUIRE→[MID_ACQUIRE]→EXTRACT→VERIFY) → SYNTHESIZE → WRITE → DONE`) remain shared vocabulary + resume/observability anchors; in v2 = positions in workflow graph, not hand-walked `Stage` enum entries.

**Stop-rule (resolves Open Q5, unchanged, pure):** subtopic done when corroborated-claim count ≥ `target_evidence`, OR pass adds `< MIN_NEW_CLAIMS` (diminishing returns), OR per-subtopic iteration cap (`depth_budget`). Pure functions (`stop_rule`, `depth_budget`) port to v2 verbatim as inline calls / function-nodes.

**Resume semantics:** dynamic workflows checkpoint every node execution; on resume, completed nodes skipped automatically. Parent nodes calling `ctx.run_node` must set `rerun_on_resume=True`. Tools may run **at least once** (possibly more) on resume — crawl/extract are idempotent (content keyed by URL in Chroma), so re-runs safe; explicit design invariant to preserve.

---

## 5. State & Persistence (resolves Open Q4 — v2: ADK-owned)

- **Source of truth = ADK session + event store.** Live stage, current subtopic, budgets, per-node completion live in ADK session; resume driven by ADK via `App(resumability_config=ResumabilityConfig(is_resumable=True))` + run's `invocation_id`.
- **crawl4ai SQLite + ChromaDB**: all crawled content + provenance + `publication_date`. Reused as-is; unaffected by session-ownership change.
- **`data/sessions/{id}/*.json` = export, not truth.** `plan.json`, `claim_ledger.json`, `draft.md` written **through** ADK state (thin after-node/after-agent callback or plugin projects ADK state to disk) for human inspection, diffing, report provenance. `stage.json` retired as resume mechanism — ADK owns resume. Final report lands at `data/reports/{session_id}.md`.
- **Migration note:** v1 `SessionStore` becomes **exporter** (write-through projection), not persistence/resume engine. See §15.

---

## 6. Checkpoint UX (resolves Open Q6 — v2: `RequestInput` HITL)

Human gates = ADK **`RequestInput`** nodes that pause workflow + resume on user response — over `adk api_server` (API/SSE), not bound to local TTY.

- **CP1 (after Planner):** `yield RequestInput(message=..., payload=plan, response_schema=ResearchPlan)` → approve/edit/reject plan.
- **CP2 (after Writer):** approve/edit/reject draft.
- **CP3 (after Acquirer, `plan.depth == "deep"` only):** inspect/steer candidate source list before extraction; add/exclude/redirect URLs. `[+]`/`[r]` edit triggers supplemental Acquirer pass (loop re-entry) before Extractor runs.
- **Pre-flight clarification** (Clarifier `needs_input`): `RequestInput` *before run start only*.

**Local-first interaction:** same workflow runs two ways — (a) thin **CLI** drives in-process for terminal use, (b) `adk api_server` for API/A2A/remote-HITL. `RequestInput`'s `response_schema` does **not** auto-coerce free-form human input, so CP3's URL-edit grammar needs small normalizing adapter (or agent node) between raw input and structured payload.

---

## 7. crawl4ai MCP Extensions (resolves Open Q3 — unchanged)

Targeted additions to existing server (extend, don't rebuild — most already shipped in Story 2):
1. **Content-similarity dedup** — collapse syndicated/mirrored pages (cosine ≥ 0.92) so independence test reliable. *(shipped: `tools/dedup.py`)*
2. **eTLD+1 field** — registrable domain on discovery output + chunk metadata. *(shipped: `app/domain.py`, `chunks.etld1`)*
3. **Seed-URL ingest** — force user URLs/files into crawl set (FR3.2). *(shipped: `tools/seed.py`)*
4. **PDF/arXiv extraction quality** — validate/improve PDF→markdown. *(shipped: routing in `crawl.py`/`adaptive_crawl.py`)*
5. **Rate-limit/backoff** — per-domain politeness + 429 retry. *(shipped: `app/ratelimit.py`)*
6. **Semantic Scholar citation-graph client** — `orchestrator/app/citation.py` httpx utility (not MCP tool), called by Acquirer for §11/§12. *(shipped)*

MCP stays tool boundary (stdio `MCPToolset`); **not** migrated to A2A — tools belong on MCP.

---

## 8. Data Flow (end-to-end)

```
user query (via CLI or A2A/api_server)
  → Clarifier (pre-flight RequestInput if needs_input)
  → Planner → ResearchPlan ──★CP1 (RequestInput)
  → research loop (per subtopic, budget-bounded, sequential):
      Acquirer: discover_urls + score_and_triage_urls → ScoredURL[]
               [if academic + deep: 2-hop S2 citation BFS → publication_date, citation_refs]
      [★CP3 if depth=deep: RequestInput, user steers source list]
      Extractor: crawl_* → SQLite + ChromaDB (content + provenance + publication_date)
      Verifier: search_chunks → claims → independence → confidence (3-tier) → temporal drift
               → ClaimLedger (status, temporal_status, confidence_score, conflict_type)
  → Writer: ClaimLedger + Plan → draft ──★CP2 (RequestInput)
  → final markdown → data/reports/{session}.md
  ADK session/event store owns state+resume throughout; data/sessions/*.json exported via callback.
```

---

## 9. Best-Practice Checklist (v2, applied)
- ✅ Single responsibility per node.
- ✅ Workflow root composes specialists; specialists never call each other.
- ✅ Typed artifact contracts; **handoffs pass IDs/structured data, not raw text**.
- ✅ Least-privilege MCP tools (Acquirer read, Extractor write, Verifier `search_chunks`); toolset built once per run + `close()`d.
- ✅ Stateless specialists; state centralized in **ADK session** (not bespoke store).
- ✅ Deterministic control flow (dynamic workflow + pure stop-rule) wrapping LLM reasoning — LLMs decide *content*, not *control*.
- ✅ Resource-aware: one hot model, sequential nodes, bounded crawl concurrency, MCP subprocess reused.
- ✅ Human-in-loop as explicit blocking `RequestInput` gates (API-resumable).
- ✅ **A2A used correctly**: one boundary at pipeline edge (local-first), specialists kept local — not A2A microservices.
- ✅ Exceptions propagate to ADK `RetryConfig`; never catch `BaseException` (breaks HITL `NodeInterruptedError`).

---

## 10. Component Inventory (v2 build targets)
| Component | v1 status | v2 action |
|-----------|-----------|-----------|
| Root workflow (`app/workflow.py`) | — | **New** — `@node` dynamic workflow; replaces `orchestrator.py` + `stage_machine.py` driver |
| Specialist nodes (Clarifier/Planner/Acquirer/Extractor/Verifier/Writer) | built (`app/agents/*`) | **Reuse** — wrap as workflow nodes; drop per-agent throwaway `Runner`/session |
| `App` + `ResumabilityConfig` | — | **New** — owns session + resume |
| A2A exposure (`to_a2a(root)` / `adk api_server --a2a`) + agent card | — | **New** — single local-first A2A server |
| Session export callback/plugin | — | **New** — projects ADK state → `data/sessions/*.json` |
| `RequestInput` checkpoints (CP1/CP2/CP3 + pre-flight) | console `checkpoint.py` | **Replace** — `RequestInput` nodes + CP3 input adapter |
| Pure policy (`stop_rule`, `depth_budget`, Verifier/Writer/Planner policy) | built | **Reuse unchanged** (function-nodes / inline) |
| Artifact schemas (Plan, ScoredURL, ClaimLedger, …) | built | **Reuse unchanged** |
| `app/llm.py` model factory | built | **Keep** + set `OLLAMA_API_BASE`; build agents once per run |
| crawl4ai MCP (§7 extensions) | shipped | **Reuse**; build `MCPToolset` once/run, `close()` at end |
| Semantic Scholar client (`app/citation.py`) | shipped | **Reuse** |
| `orchestrator.py` / `stage_machine.py` driver / `SessionStore` as truth / `pipeline.py` `_run_sync` | built (v1) | **Retire / repurpose** (SessionStore → exporter) — §15 |
| SQLite/Chroma/Ollama stores | existing | **Reuse** |

---

## 11. Citation Network Traversal Design (P1) — unchanged

**Trigger:** `source_class == academic` AND `plan.depth == "deep"`.

```
for each academic ScoredURL in initial discovery:
    paper_id = extract_paper_id(url)            # arXiv ID or S2 paper ID
    if paper_id:
        hop1 = semantic_scholar_api(paper_id).references
        for candidate in hop1:
            if llm_score(candidate.title+abstract, subtopic_question) >= CITATION_RELEVANCE_THRESHOLD:
                enqueue(candidate, depth=2)
        hop2 = [semantic_scholar_api(c).references for c in hop1_enqueued]  # relevance-gated too
```

**Bounds:** max 2 hops; `CITATION_RELEVANCE_THRESHOLD` (default 0.7); per-subtopic BFS budget cap; bounded queue. **Resource:** LLM scoring uses resident model (no swap); S2 calls = lightweight JSON. **Output:** enriched `ScoredURL[]` (`publication_date`, `citation_refs`) → normal Extractor pipeline.

---

## 12. Enhanced Verification Design (P2 + P4) — unchanged

### 12.1 Contradiction Confidence Scoring (P2)
Each contradiction carries `confidence_score: float [0,1]` + `conflict_type: factual|methodological|temporal_drift`. **Three-tier additive rubric** (each 0–0.33): (1) source independence (different eTLD+1 + low cosine → higher; same domain = 0), (2) recency delta (both recent → higher; large gap → drift, not factual), (3) methodological explicitness (cites data/methods → higher; LLM-supplied). Appendix sorted by score descending.

### 12.2 Temporal Drift Detection (P4)
`publication_date` flows S2 → `ScoredURL` → chunk metadata → Verifier. Date gap ≥ `TEMPORAL_DRIFT_THRESHOLD_MONTHS` (18) → `conflict_type="temporal_drift"`; older side `temporal_status="dated"`, newer `"current"`. Missing date → `"uncertain"`, never classified as drift. Report: separate "Temporal Drift" sub-section, informational framing ("field has evolved since [date]") — AC7.

---

## 13. A2A Exposure & Local-First Topology (NEW)

> **As built (E4/P5, 2026-06-27):** landed exposure = **unified `get_fast_api_app(a2a=True)`** factory (`app/server.py`) — one localhost:8001 process serving **both** REST (`/run_sse`, session CRUD, `/list-apps`) **and** A2A protocol (card + RPC), chosen over `to_a2a` (A2A-only, no `/run_sse`). App name landed as **`localgpt_research`** (`deep_research` below = design-time placeholder); card at `/a2a/localgpt_research/.well-known/agent-card.json` with `text/plain` in / `text/markdown` out + A2A new-executor extension advertised. ADK 2.3.0 `json`-shadow bug 404s auto-mounted card → hand-rolled `_mount_a2a` (future-ADK-safe guard). HITL pause/resume proven over A2A protocol itself (HARD GATE) + REST resume-by-`invocation_id`. CLI = `app/cli.py`; `RemoteA2aAgent(use_legacy=False)` example = `app/a2a_client_example.py`. Specialists NOT split onto own servers (seam only). Detail: [../claudedocs/spawn_v1_to_v2_migration.md] E4 + [../claudedocs/E4_execution_plan.md].

**Intent:** A2A = project's reason for choosing ADK, applied where it pays off — at **pipeline boundary**, not between six co-resident specialists (ADK guidance + RAM constraint, §0/§2).

**Exposure (one of two equivalent paths):**
- **`to_a2a(root_agent, port=8001)`** — wraps root workflow into A2A Starlette app served by `uvicorn`; auto-generates agent card in-memory. Tightest control over what is exposed.
- **`adk api_server --a2a <folder>`** with `agent-card.json` — also gives `adk web` for debug/test; can host multiple agents from one parent folder. Preferred when api_server is already runtime.

Agent card published at well-known path (`…/a2a/deep_research/.well-known/agent-card.json`), advertising pipeline's skill ("local deep-research → vetted markdown report") + `text/plain` in / `text/markdown` (or `application/json`) out.

**Local-first consumption (explicit requirement):** exposed server binds to **localhost**. Local consumer reaches without leaving machine, two ways:
1. **Direct HTTP** — `POST /run_sse` (run + stream), `RequestInput` pauses for CP1/CP2/CP3, resume via `invocation_id`. Thin CLI speaks this.
2. **`RemoteA2aAgent`** — another local ADK agent consumes pipeline as sub-agent:
   ```python
   deep_research = RemoteA2aAgent(
       name="deep_research",
       description="Local deep-research pipeline → vetted markdown report.",
       agent_card=f"http://localhost:8001/a2a/deep_research{AGENT_CARD_WELL_KNOWN_PATH}",
       use_legacy=False,
   )
   ```

**Future split seam (not built now):** because specialists communicate only through typed artifacts, any single heavy specialist (e.g. Acquirer + citation BFS) *could* later be lifted onto own `A2AServer` on another box and consumed via `RemoteA2aAgent`, with no change to contracts. Internal A2A **not** adopted today (no concurrency gain on one hot model) — only seam preserved.

**Dependency:** `google-adk[a2a]`.

---

## 14. ADK 2.x Runtime & Dynamic-Workflow Substrate (NEW)

- **`App` object** = top-level container: `App(name="deep_research", root_agent=root_agent, resumability_config=ResumabilityConfig(is_resumable=True), plugins=[...])`.
- **Dynamic workflow** = `Workflow(edges=[("START", research)])` with `@node` functions; data passes by `ctx.run_node()` return value (no manual session plumbing).
- **Cross-cutting concerns via callbacks/plugins** (review §1.2): structured logging/telemetry + `data/sessions/*.json` export go in `BeforeAgentCallback`/`AfterAgentCallback` or `App`-level Plugin — **not** execution overrides (2.0 Workflow Runtime bypasses `_run_async_impl` overrides).
- **Error handling (review §1.3):** let standard exceptions propagate to `RetryConfig(max_attempts=…)`; never wrap tool bodies in broad `except Exception:` (disables retries) or catch `BaseException` (traps `NodeInterruptedError`, breaking HITL pause).
- **Observability:** ADK traces/metrics/logging + optional tracing integration; multi-pass research loop benefits from per-node spans.
- **Sequential by constraint, parallel-ready by substrate:** subtopic research stays sequential under 16 GB limit, but dynamic workflows support `asyncio.gather(*[ctx.run_node(worker, st) ...])` with resume re-running only failed workers — clean upgrade path if hardware envelope grows.

---

## 15. Migration: v1 (hand-rolled) → v2 (ADK 2.x) (NEW)

Code today = v1 (Stories 1–5: `orchestrator.py`, `stage_machine.py` driver, `pipeline.py` `_run_sync`, `SessionStore` as truth, console `checkpoint.py`). Deterministic policy + agents + schemas + MCP carry over unchanged; only orchestration shell changes. Suggested sequence (design-level; execute via `/sc:implement`).

> **Status (2026-06-27):** steps 1–6 ✅ done (committed to `dev`, 273 orchestrator + 43 MCP green; all 4 HARD GATES passed); step 7 (retire v1 shell) + real-HW acceptance pending = E5. Per-step execution detail in [../claudedocs/spawn_v1_to_v2_migration.md] *Execution Log*.

1. ✅ **Tier 0 correctness first** (review §0): set `OLLAMA_API_BASE`; pin `google-adk>=2.3,<3`; build each `MCPToolset` once/run + `close()`. Independent of rewrite, de-risks env.
2. ✅ **Stand up `App` + one-node `Workflow`** wrapping existing Clarifier→Planner path with `RequestInput` CP1; enable `ResumabilityConfig`. Validate ADK-owned resume on trivial slice. Keep `SessionStore` writing in parallel (write-through) for safety.
3. ✅ **Port research loop** (`while` + `stop_rule` + subtopic iteration) into `@node`; wrap Acquirer/Extractor/Verifier as nodes via `ctx.run_node`; reuse policy functions verbatim.
4. ✅ **Replace checkpoints** with `RequestInput` (CP2, CP3 + CP3 input adapter); delete console `checkpoint.py` once parity proven *(delete deferred to step 7 — still imported by v1 shell)*.
5. ✅ **Flip session ownership** to ADK; demote `SessionStore` to export callback/plugin (`SessionExporterPlugin`); retire `stage.json`-based resume.
6. ✅ **Expose A2A** — landed as unified `get_fast_api_app(a2a=True)` (REST + A2A in one localhost process) over `to_a2a`; add agent card; wire thin CLI to `/run_sse` (§13 "As built").
7. ⏳ **Retire** `orchestrator.py` + `stage_machine.py` driver + `pipeline.py` `_run_sync` + console `checkpoint.py` once workflow = sole driver (= E5.S1).

Acceptance: AC1–AC7 must still pass end-to-end; resume-by-`invocation_id` + localhost A2A round-trip = new acceptance checks.

---

**Next step:** §15 steps 1–6 done (E0–E4 on `dev`). Remaining = **E5** — retire v1 shell (§15 step 7 / spawn-doc E5.S1) then prove migration E2E on real hardware (AC1–AC7 + resume + A2A round-trip + OOM; needs E0.S3 model pull). Plans: [../claudedocs/workflow_v1_to_v2_migration.md] (P6/P7) + [../claudedocs/spawn_v1_to_v2_migration.md] (E5).