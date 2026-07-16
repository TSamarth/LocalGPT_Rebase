"""
Extractor agent (Story 3.5, FR4).

The Extractor is the *content* worker of the research loop: it takes the triaged
``ScoredURL`` list the Acquirer produced and crawls each one through the crawl4ai
MCP server, choosing the MCP tool that matches the URL's assigned
``CrawlStrategy``. The crawled, content-filtered chunks are embedded into ChromaDB
by the MCP server; the Extractor hands downstream agents page IDs, never raw text
(architecture.md §3 — agents handoff IDs, not text).

Because it HOLDS MCP write tools it is a *tool* agent (architecture.md §1): we
pass ``tools=[toolset]`` and leave ``output_schema=None``. ADK forbids combining a
forced output schema with tools/transfer, and ``build_agent`` enforces that.

Date propagation (T0.4, architecture.md §3.4/§11). ``ScoredURL.publication_date``
is metadata the Acquirer already resolved (Semantic Scholar / arXiv). The
Extractor's job is strictly *passthrough*: when a date is present it is copied
into the chunk metadata the crawl writes to Chroma, so the Verifier can later read
it back off ``SourceRef.publication_date`` for temporal-drift scoring (FR5.6). The
Extractor must NEVER invent or infer a date — if the Acquirer didn't supply one,
none is written. ``chunk_metadata_for`` is the deterministic helper that encodes
this rule and is unit-tested in isolation.

MCP wiring is lazy + injectable. The ``mcp`` package is an optional ADK extra and
is not a hard dependency of the orchestrator env, so the real ``MCPToolset`` and
its ``StdioConnectionParams``/``StdioServerParameters`` are imported *inside*
``_build_default_toolset`` only. Unit tests inject a fake ``BaseToolset`` (or
``None``) and never touch that import, keeping the whole suite offline.
"""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from ..llm import DETERMINISTIC_CONFIG, build_agent
from ..schemas import CrawlStrategy, ScoredURL

# MCP crawl tools the Extractor is allowed to call, keyed by the strategy the
# Acquirer/triage assigned. Values are the crawl4ai MCP tool names (CrawlStrategy
# mirrors them 1:1). ``SKIP`` deliberately has no tool — those URLs are dropped.
STRATEGY_TOOL_NAMES: dict[CrawlStrategy, str] = {
    CrawlStrategy.ADAPTIVE: "adaptive_crawl",
    CrawlStrategy.DEEP: "deep_crawl",
    CrawlStrategy.CRAWL: "crawl_url",
}

_EXTRACTOR_ROLE_PROMPT = """\
You are the Extractor in a local deep-research pipeline.

Input: a list of triaged candidate URLs (ScoredURL objects). Each carries a
`strategy` field that names exactly which crawl tool to use, a `query` field
(the original research question), plus an optional `publication_date`.

Your job, for EACH input URL, in order:
1. Read its `strategy`. Map it to the MCP crawl tool to call:
     - strategy "adaptive_crawl" -> call the `adaptive_crawl` tool
     - strategy "deep_crawl"     -> call the `deep_crawl` tool
     - strategy "crawl_url"      -> call the `crawl_url` tool
     - strategy "skip"           -> do NOT crawl it; move on.
2. Call that one tool on that one URL, passing the URL's `query` field as the
   tool's `query` argument UNCHANGED. NEVER substitute the page title, a URL
   fragment, or any other text for `query` — it must be the exact research
   question carried on the ScoredURL. The MCP server filters the content,
   chunks it, embeds it into the vector store, and returns a page id:
     - `adaptive_crawl` / `deep_crawl` -> a `page_ids` list in the response
     - `crawl_url`                     -> a single `page_id` field in the response
3. If the URL has a `publication_date`, pass it through unchanged in the crawl's
   chunk metadata so it is stored alongside the chunks. NEVER guess, infer, or
   fabricate a date. If a URL has no `publication_date`, write no date for it.

Rules:
- Use ONLY the crawl tool named by each URL's `strategy`. Do not substitute a
  different tool or crawl a URL marked `skip`.
- Always pass the ScoredURL's `query` field as the tool's `query` argument —
  never the page title or anything else.
- Do not summarise or rewrite page content yourself — the MCP tools do the
  content filtering. Hand downstream the returned page/chunk IDs, not raw text.
- When every non-skipped URL has been crawled, report the page IDs you created
  (collect every tool's `page_id`/`page_ids` into one flat list).
- Your final response MUST be ONLY a JSON array of the created page-id strings —
  e.g. ["page-1", "page-2"] — or [] if nothing was crawled. Never answer the
  research question, summarise findings, or emit any prose before or after it.
"""


def chunk_metadata_for(scored: ScoredURL) -> dict:
    """Chunk-metadata payload for a crawled ``ScoredURL`` (date passthrough only).

    Copies ``scored.publication_date`` into the metadata the crawl writes to
    Chroma **only when it is present**. This is strict passthrough of metadata the
    Acquirer already resolved — the Extractor performs no date inference, so a
    ``ScoredURL`` without a date yields metadata without a ``publication_date``
    key (FR4, architecture.md §3.4/§11).

    The date is ISO-encoded (``date.isoformat()``) so the payload is plain-JSON
    and round-trips cleanly through the MCP boundary and ChromaDB metadata, which
    do not accept ``datetime.date`` objects.
    """
    metadata: dict = {"source_url": scored.url}
    if scored.publication_date is not None:
        metadata["publication_date"] = scored.publication_date.isoformat()
    return metadata


def _build_default_toolset() -> BaseToolset:
    """Construct the real crawl4ai ``MCPToolset`` (stdio subprocess).

    ``tool_filter`` restricts the agent to the crawl tools it is permitted to
    write with; the read/triage tools belong to the Acquirer (least-privilege,
    §1). The shared construction body (and its lazy ``mcp`` import) lives in
    ``crawl4ai_toolset.build_crawl4ai_toolset``.
    """
    from .crawl4ai_toolset import build_crawl4ai_toolset

    return build_crawl4ai_toolset(list(STRATEGY_TOOL_NAMES.values()))


def build_extractor(toolset: Optional[BaseToolset] = None) -> LlmAgent:
    """Build the Extractor as a role-prompted MCP-tool agent.

    Args:
        toolset: MCP toolset providing the crawl tools. Defaults to the real
            crawl4ai ``MCPToolset`` (stdio subprocess at ``config.MCP_SERVER_CWD``);
            tests inject a fake ``BaseToolset`` so they run offline.

    Returns:
        An ``LlmAgent`` with the crawl toolset wired in and ``output_schema=None``
        (tool agents cannot also force an output schema — see ``build_agent``).
    """
    active_toolset = toolset if toolset is not None else _build_default_toolset()
    return build_agent(
        name="extractor",
        role_prompt=_EXTRACTOR_ROLE_PROMPT,
        tools=[active_toolset],
        output_key="extracted_page_ids",
        generate_content_config=DETERMINISTIC_CONFIG,
    )
