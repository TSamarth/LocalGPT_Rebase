"""
Acquirer agent (Story 3.4, FR3.1/FR3.4/FR3.5) — turns a subtopic into a triaged,
deduped, date-enriched ``list[ScoredURL]``.

The Acquirer is the *discovery* worker of the research loop (architecture.md §3.4).
Given a subtopic's question(s) and its ``source_classes`` (plus the plan ``depth``),
it drives the crawl4ai MCP READ tools — ``discover_urls`` (SerpAPI + DDG + arXiv)
then ``score_and_triage_urls`` — to produce candidate ``ScoredURL`` records. It
holds ONLY those read tools; the ``crawl_*`` write tools belong to the Extractor
(least-privilege, §1).

Because it HOLDS MCP tools it is a *tool* agent (architecture.md §1): we pass
``tools=[toolset]`` and leave ``output_schema=None``. ADK forbids combining a
forced output schema with tools/transfer, and ``build_agent`` enforces that.

Citation BFS enrichment (architecture.md §11). The distinguishing Acquirer logic
runs only for ``source_class == academic`` AND ``plan.depth == "deep"``: for each
academic ``ScoredURL`` it normalises a paper id (``citation.extract_paper_id``)
and walks the Semantic Scholar citation graph (2-hop ``references`` BFS via
``citation.CitationClient.bfs``), relevance-gated by an injectable LLM scorer. The
seed paper's ``citation_refs`` are set to the followed reference ids, and each
discovered paper becomes a new ``ScoredURL`` carrying its ``publication_date``
(feeds the Verifier's temporal-drift check, §12). The BFS *client* and *scorer*
are injected so the whole enrichment path is deterministic and offline-testable;
the Acquirer never reimplements ``citation.py`` — it only drives it.

MCP wiring is lazy + injectable, exactly like the Extractor: the real
``MCPToolset`` and its ``StdioConnectionParams``/``StdioServerParameters`` are
imported *inside* ``_build_default_toolset`` only (``mcp`` is an optional ADK
extra). Unit tests inject a fake ``BaseToolset`` and never touch that import.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional, Union

from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from ..citation import CitationClient, RelevanceScorer, extract_paper_id
from ..config import config
from ..jsonio import invoke_json_with_retry, loads_first_json
from ..llm import DETERMINISTIC_CONFIG, build_agent
from ..schemas import CrawlStrategy, Depth, ResearchPlan, ScoredURL, SourceClass, Subtopic

logger = logging.getLogger("orchestrator.agents.acquirer")

# MCP READ tools the Acquirer is permitted to call (discovery + triage). The
# crawl_* write tools deliberately stay out of this filter — those belong to the
# Extractor (architecture.md §1, least-privilege).
DISCOVER_TOOL = "discover_urls"
TRIAGE_TOOL = "score_and_triage_urls"
ACQUIRER_TOOL_NAMES: list[str] = [DISCOVER_TOOL, TRIAGE_TOOL]

#: state/output key the agent writes its triaged ``ScoredURL`` JSON under.
OUTPUT_KEY = "scored_urls"

#: Agent name (also used as the ADK app name for the default runner).
AGENT_NAME = "acquirer"

# A runner takes the subtopic question and returns the model's triaged output,
# either as a raw JSON string or an already-parsed list/mapping. Async so the
# default (ADK Runner over Ollama) and test doubles share one signature.
AcquirerRunner = Callable[[str], Awaitable[Union[str, list, dict]]]

ROLE_PROMPT = """\
You are the Acquirer in a local deep-research pipeline. You receive ONE subtopic
question (plus the research depth and the source classes it should draw evidence
from) and produce a triaged list of candidate URLs to crawl. You do NOT crawl
pages yourself and you do NOT invent URLs — you only discover and triage.

Your input arrives as plain text in this exact shape — the first two lines are
metadata, the rest (after "Question:") is the actual research question to search
for. Never search for the metadata lines themselves:

  Depth: shallow | normal | deep
  Source classes: web, academic, code, seed (comma-separated subset)
  Question: <the subtopic question — this is what you pass to discover_urls>

Your job, in order:
1. FIRST, call the `discover_urls` tool with the subtopic question to gather candidate
   URLs across the requested source classes (web search, DuckDuckGo, arXiv),
   max_results_per_source = 3 and max_total =10.
2. Second, call the `score_and_triage_urls` tool on those candidates. It scores each URL
   for relevance and assigns a crawl `strategy`.
3. Return the triaged candidates as a JSON array of ScoredURL records. Each record:

   {
     "url": "https://...",
     "score": 0.0,                         // 0-1 relevance from triage
     "strategy": "adaptive_crawl" | "deep_crawl" | "crawl_url" | "skip",
     "source": "the discovery source that found it",
     "also_in": [],                        // other sources that also found it
     "etld1": "the registrable domain"
   }

Example of a correct run (your only allowed actions):
  input:  Depth: normal
          Source classes: web
          Question: What are the trade-offs of vector databases?
  step 1: call tool discover_urls(query="What are the trade-offs of vector databases?", max_total=10, max_results_per_source=3, ...)
  step 2: call tool score_and_triage_urls(urls=[...the discovered URLs...])
  step 3: final response is EXACTLY the triaged records as a JSON array:
          [{"url": "https://example.com/a", "score": 0.9, "strategy": "crawl_url",
            "source": "web_search", "also_in": [], "etld1": "example.com"}]

Rules:
- Your FIRST action MUST be a `discover_urls` tool call. Never respond with text
  before calling it. Your ONLY job is to call `discover_urls` then
  `score_and_triage_urls` and return their triaged result as JSON — you never
  answer, summarize, or discuss the question itself.
- You hold no crawl tools — never attempt to crawl or fetch page content.
- Deduplicate URLs: one record per URL. If several discovery sources found the
  same URL, keep one record and list the extra sources in `also_in`.
- Do not fabricate scores or strategies — use what the triage tool returns.
- Output ONLY the JSON array of ScoredURL records and nothing else.
- CRITICAL: If a tool fails or returns no results, output an empty JSON array: []
- CRITICAL: Never output prose, markdown fences, or explanations — raw JSON only.
"""


def build_acquirer_prompt(question: str, *, depth: Depth, source_classes) -> str:
    """Render the plain-text prompt the Acquirer's ``ROLE_PROMPT`` expects.

    Depth and source classes are conveyed as a short natural-language prefix
    (not a JSON envelope) so a small local model can read them the same way it
    reads the question itself — mirrors how the Verifier receives plain
    ``subtopic.question`` text.
    """
    classes = ", ".join(c.value for c in source_classes)
    return f"Depth: {depth.value}\nSource classes: {classes}\nQuestion: {question}"


# ── citation BFS guard (architecture.md §11) ──────────────────────────────────
def should_run_citation_bfs(source_classes, depth: Depth) -> bool:
    """Predicate gating the citation-BFS enrichment (architecture.md §11).

    Returns ``True`` only when the subtopic draws on ``academic`` sources AND the
    plan ``depth`` is ``DEEP`` — the sole condition under which the Acquirer walks
    the Semantic Scholar citation graph. Pure and side-effect free so the gate is
    trivially testable and the same call protects every enrichment entry point.
    """
    return SourceClass.ACADEMIC in source_classes and depth == Depth.DEEP


def _s2_url_for(meta: dict) -> str:
    """Stable URL for a discovered S2 paper, preferring its native paper id.

    Uses the Semantic Scholar paper page so the URL is dedup-stable and resolvable;
    falls back to the normalised query id when the native ``s2_paper_id`` is absent.
    """
    pid = meta.get("s2_paper_id") or meta.get("paper_id") or ""
    return f"https://www.semanticscholar.org/paper/{pid}"


def parse_scored_urls(raw: Union[str, list, dict, ScoredURL]) -> list[ScoredURL]:
    """Validate raw model output into a ``list[ScoredURL]`` (mirrors ``parse_plan``).

    Accepts a JSON string (array or single object), a list of mappings/objects, a
    single mapping, or already-built ``ScoredURL`` instances — so callers can hand
    back whatever the runner produced. Returns validated ``ScoredURL`` records.
    """
    logger.debug("acquirer raw output: %r", raw)
    if isinstance(raw, str):
        raw = loads_first_json(raw)
    if isinstance(raw, ScoredURL):
        return [raw]
    if isinstance(raw, dict):
        return [ScoredURL.model_validate(raw)]
    out: list[ScoredURL] = []
    for item in raw:
        if isinstance(item, ScoredURL):
            out.append(item)
        else:
            out.append(ScoredURL.model_validate(item))
    return out


async def enrich_with_citations(
    scored_urls: list[ScoredURL],
    *,
    subtopic_question: str,
    depth: Depth,
    source_classes,
    client: CitationClient,
    scorer: Optional[RelevanceScorer] = None,
) -> list[ScoredURL]:
    """Walk the citation graph for academic seeds and append discovered papers.

    Pure async orchestration over the *injected* ``CitationClient`` (the Acquirer
    never reimplements ``citation.py``). When the guard
    (:func:`should_run_citation_bfs`) is not satisfied the input list is returned
    unchanged. Otherwise, for each academic ``ScoredURL``:

    * normalise a paper id via :func:`citation.extract_paper_id` (skip when None);
    * run a ``direction="references"`` BFS (``config.CITATION_BFS_HOPS`` hops,
      relevance-gated by ``scorer`` at ``config.CITATION_RELEVANCE_THRESHOLD``);
    * set the seed's ``citation_refs`` to the followed (non-seed) paper ids and
      copy its ``publication_date`` from S2 metadata when missing;
    * map each newly discovered paper to a NEW ``ScoredURL`` carrying its
      ``publication_date``, deduping against URLs already present.

    Returns a NEW list (inputs are deep-copied) so the function stays pure.
    """
    if not should_run_citation_bfs(source_classes, depth):
        return list(scored_urls)

    enriched: list[ScoredURL] = [s.model_copy(deep=True) for s in scored_urls]
    seen_urls: set[str] = {s.url for s in enriched}

    for seed in enriched:
        paper_id = extract_paper_id(seed.url)
        if paper_id is None:
            continue

        discovered = await client.bfs(
            paper_id,
            subtopic_question=subtopic_question,
            direction="references",
            max_hops=config.CITATION_BFS_HOPS,
            scorer=scorer,
            relevance_threshold=config.CITATION_RELEVANCE_THRESHOLD,
        )
        if not discovered:
            continue

        seed_meta = discovered.get(paper_id)
        followed_ids: list[str] = [pid for pid in discovered if pid != paper_id]

        # Enrich the seed: record the followed references and fill a missing date.
        seed.citation_refs = followed_ids
        if seed_meta is not None and seed.publication_date is None:
            seed.publication_date = seed_meta.get("publication_date")

        # Map every newly discovered paper to a fresh ScoredURL (deduped).
        for pid, meta in discovered.items():
            if pid == paper_id:
                continue
            url = _s2_url_for(meta)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            enriched.append(
                ScoredURL(
                    url=url,
                    score=seed.score,
                    strategy=CrawlStrategy.CRAWL,
                    source="citation_bfs",
                    etld1="semanticscholar.org",
                    publication_date=meta.get("publication_date"),
                    citation_refs=[],
                )
            )

    return enriched


def _build_default_toolset() -> BaseToolset:
    """Construct the real crawl4ai ``MCPToolset`` (stdio subprocess), READ tools only.

    ``tool_filter`` restricts the agent to the discovery/triage read tools; the
    ``crawl_*`` write tools belong to the Extractor (least-privilege, §1). The
    shared construction body (and its lazy ``mcp`` import) lives in
    ``crawl4ai_toolset.build_crawl4ai_toolset``.
    """
    from .crawl4ai_toolset import build_crawl4ai_toolset

    return build_crawl4ai_toolset(list(ACQUIRER_TOOL_NAMES))


def build_acquirer(toolset: Optional[BaseToolset] = None) -> LlmAgent:
    """Build the Acquirer as a role-prompted MCP-tool agent.

    Args:
        toolset: MCP toolset providing the ``discover_urls`` / ``score_and_triage_urls``
            read tools. Defaults to the real crawl4ai ``MCPToolset`` (stdio
            subprocess at ``config.MCP_SERVER_CWD``); tests inject a fake
            ``BaseToolset`` so they run offline.

    Returns:
        An ``LlmAgent`` with the read toolset wired in and ``output_schema=None``
        (tool agents cannot also force an output schema — see ``build_agent``).
    """
    active_toolset = toolset if toolset is not None else _build_default_toolset()
    return build_agent(
        name=AGENT_NAME,
        role_prompt=ROLE_PROMPT,
        tools=[active_toolset],
        output_key=OUTPUT_KEY,
        generate_content_config=DETERMINISTIC_CONFIG,
    )


async def _default_runner(subtopic_question: str) -> str:
    """Drive the real Acquirer agent through ADK over the local Ollama model.

    Built lazily and only used when no ``runner`` is injected, so importing this
    module (and unit-testing it) never requires a running Ollama or MCP server.
    Returns the raw JSON text the agent emitted under ``OUTPUT_KEY``.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = build_acquirer()
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=AGENT_NAME, user_id="orchestrator", session_id="acquire"
    )
    runner = Runner(agent=agent, app_name=AGENT_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=subtopic_question)])
    final_text = ""
    for event in runner.run(
        user_id="orchestrator", session_id="acquire", new_message=message
    ):
        logger.debug("acquirer event: %s", event)
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)

    session = await session_service.get_session(
        app_name=AGENT_NAME, user_id="orchestrator", session_id="acquire"
    )
    if session is not None:
        stored = session.state.get(OUTPUT_KEY)
        if isinstance(stored, (list, dict)):
            return json.dumps(stored)
        if isinstance(stored, str) and stored.strip():
            return stored
    return final_text


async def acquire(
    subtopic: Subtopic,
    plan: ResearchPlan,
    *,
    runner: Optional[AcquirerRunner] = None,
    citation_client: Optional[CitationClient] = None,
    scorer: Optional[RelevanceScorer] = None,
) -> list[ScoredURL]:
    """Discover + triage URLs for ``subtopic``, then citation-enrich when warranted.

    Runs the Acquirer agent (model owns discovery/triage judgement) to obtain a
    triaged ``list[ScoredURL]``, then applies :func:`enrich_with_citations` when
    :func:`should_run_citation_bfs` passes for this subtopic's ``source_classes``
    and the plan's ``depth``.

    ``runner`` and ``citation_client`` are injectable so the full parse →
    enrichment path runs offline against canned output (unit tests, replay). When
    omitted, the default runner drives the real agent through ADK + Ollama and a
    fresh ``CitationClient`` (real S2 API) is created for enrichment.

    ``scorer`` is the optional LLM relevance gate threaded into the BFS (§11).
    """
    invoke = runner or _default_runner
    prompt = build_acquirer_prompt(
        subtopic.question, depth=plan.depth, source_classes=subtopic.source_classes
    )
    raw = await invoke_json_with_retry(invoke, prompt)
    try:
        scored_urls = parse_scored_urls(raw)
    except ValueError:
        # The model returned no parseable JSON even after the JSON-only re-ask
        # (e.g. crawl tools timed out and it answered in prose). Degrade to "no
        # candidates" so the run survives rather than crashing the pipeline (E0.S2).
        scored_urls = []

    if not should_run_citation_bfs(subtopic.source_classes, plan.depth):
        return scored_urls

    if citation_client is not None:
        return await enrich_with_citations(
            scored_urls,
            subtopic_question=subtopic.question,
            depth=plan.depth,
            source_classes=subtopic.source_classes,
            client=citation_client,
            scorer=scorer,
        )

    async with CitationClient() as client:
        return await enrich_with_citations(
            scored_urls,
            subtopic_question=subtopic.question,
            depth=plan.depth,
            source_classes=subtopic.source_classes,
            client=client,
            scorer=scorer,
        )
