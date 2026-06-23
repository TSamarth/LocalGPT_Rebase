"""
Offline unit tests for the Acquirer agent (T3.4, FR3.1/FR3.4/FR3.5).

No live Ollama, MCP subprocess, or network. ``build_agent`` constructs the
``LlmAgent`` without contacting Ollama (LiteLlm imports litellm lazily on the
first generate call); a fake ``BaseToolset`` is injected so the real
``MCPToolset`` / ``mcp`` stdio path is never exercised; and the citation BFS runs
against a tiny ``httpx.MockTransport`` graph (same style as test_citation.py).
``asyncio_mode = "auto"`` (pyproject) means ``async def test_*`` needs no marker.
"""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from app.agents.acquirer import (
    ACQUIRER_TOOL_NAMES,
    acquire,
    build_acquirer,
    enrich_with_citations,
    parse_scored_urls,
    should_run_citation_bfs,
)
from app.citation import CitationClient
from app.schemas import (
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceClass,
    Subtopic,
)


# ── Fake MCP toolset ──────────────────────────────────────────────────────────
class FakeToolset(BaseToolset):
    """Minimal in-memory ``BaseToolset`` so the agent builds with no stdio subprocess."""

    async def get_tools(self, readonly_context=None):  # noqa: D401 - test stub
        return []


# ── Fake citation graph served via MockTransport (mirrors test_citation.py) ────
# Graph (references direction):  A -> [B, C];  B -> [D]
_A = "arXiv:2301.00001"
_B = "b" * 40
_C = "c" * 40
_D = "d" * 40

_GRAPH = {
    _A: {
        "paperId": "a" * 40,
        "title": "Paper A",
        "publicationDate": "2023-01-01",
        "references": [{"paperId": _B}, {"paperId": _C}],
        "citations": [],
    },
    _B: {
        "paperId": _B,
        "title": "Paper B",
        "publicationDate": "2022-06-01",
        "references": [{"paperId": _D}],
        "citations": [],
    },
    _C: {
        "paperId": _C,
        "title": "Paper C",
        "year": 2020,
        "references": [],
        "citations": [],
    },
    _D: {
        "paperId": _D,
        "title": "Paper D",
        "publicationDate": "2019-03-03",
        "references": [],
        "citations": [],
    },
}


def _graph_handler(request: httpx.Request) -> httpx.Response:
    paper_id = request.url.path.split("/paper/")[-1]
    record = _GRAPH.get(paper_id)
    if record is None:
        return httpx.Response(404, json={"error": "not found"})
    return httpx.Response(200, json=record)


def _make_client(handler=_graph_handler) -> CitationClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)

    async def _sleep(_seconds: float) -> None:  # noqa: ANN001 - no real waiting
        return None

    return CitationClient(http, sleep=_sleep)


def _academic_seed() -> ScoredURL:
    return ScoredURL(
        url="https://arxiv.org/abs/2301.00001",
        score=0.8,
        strategy=CrawlStrategy.CRAWL,
        source="arxiv",
    )


def _subtopic(source_classes) -> Subtopic:
    return Subtopic(
        id="s1",
        question="what is X?",
        target_evidence=3,
        source_classes=source_classes,
    )


def _plan(depth: Depth) -> ResearchPlan:
    return ResearchPlan(subtopics=[_subtopic([SourceClass.ACADEMIC])], depth=depth)


# ── should_run_citation_bfs: truth table ──────────────────────────────────────
def test_guard_academic_and_deep_is_true():
    assert should_run_citation_bfs([SourceClass.ACADEMIC], Depth.DEEP) is True


def test_guard_academic_but_normal_is_false():
    assert should_run_citation_bfs([SourceClass.ACADEMIC], Depth.NORMAL) is False
    assert should_run_citation_bfs([SourceClass.ACADEMIC], Depth.SHALLOW) is False


def test_guard_web_and_deep_is_false():
    assert should_run_citation_bfs([SourceClass.WEB], Depth.DEEP) is False


def test_guard_mixed_classes_with_academic_and_deep_is_true():
    assert should_run_citation_bfs(
        [SourceClass.WEB, SourceClass.ACADEMIC], Depth.DEEP
    ) is True


# ── parse_scored_urls: roundtrip + raw JSON ───────────────────────────────────
def test_parse_scored_urls_from_json_array():
    raw = json.dumps(
        [
            {"url": "https://a.com", "score": 0.9, "strategy": "crawl_url"},
            {"url": "https://b.com", "score": 0.5, "strategy": "skip"},
        ]
    )
    parsed = parse_scored_urls(raw)
    assert [s.url for s in parsed] == ["https://a.com", "https://b.com"]
    assert parsed[0].strategy == CrawlStrategy.CRAWL
    assert parsed[1].strategy == CrawlStrategy.SKIP


def test_parse_scored_urls_roundtrip_objects():
    seed = _academic_seed()
    parsed = parse_scored_urls([seed])
    assert parsed == [seed]


def test_parse_scored_urls_single_dict_and_single_object():
    one = parse_scored_urls({"url": "https://a.com", "score": 0.1, "strategy": "crawl_url"})
    assert len(one) == 1 and one[0].url == "https://a.com"

    obj = parse_scored_urls(_academic_seed())
    assert len(obj) == 1 and obj[0].url == _academic_seed().url


# ── build_acquirer: tool agent wiring ─────────────────────────────────────────
def test_build_acquirer_returns_tool_agent_with_injected_toolset():
    toolset = FakeToolset()
    agent = build_acquirer(toolset=toolset)

    assert isinstance(agent, LlmAgent)
    assert agent.tools, "acquirer must hold its read toolset"
    assert toolset in agent.tools
    # Tool agents must NOT force an output schema (ADK constraint).
    assert agent.output_schema is None


def test_build_acquirer_does_not_construct_real_toolset_when_injected(monkeypatch):
    import app.agents.acquirer as acquirer_mod

    def _boom():
        raise AssertionError("_build_default_toolset must not run when a toolset is injected")

    monkeypatch.setattr(acquirer_mod, "_build_default_toolset", _boom)
    agent = build_acquirer(toolset=FakeToolset())
    assert isinstance(agent, LlmAgent)


def test_build_acquirer_role_prompt_names_read_tools():
    agent = build_acquirer(toolset=FakeToolset())
    instruction = agent.instruction
    # The two read tools the Acquirer is allowed to drive are named in the prompt.
    for tool_name in ACQUIRER_TOOL_NAMES:
        assert tool_name in instruction
    # The prompt must make clear the Acquirer holds NO crawl/write tools (those
    # are the Extractor's) — least-privilege, architecture.md §1.
    assert "no crawl tools" in " ".join(instruction.lower().split())


# ── enrich_with_citations ─────────────────────────────────────────────────────
async def test_enrich_populates_dates_and_refs():
    client = _make_client()
    seeds = [_academic_seed()]

    enriched = await enrich_with_citations(
        seeds,
        subtopic_question="what is X?",
        depth=Depth.DEEP,
        source_classes=[SourceClass.ACADEMIC],
        client=client,
    )
    await client.aclose()

    seed = enriched[0]
    # Seed: citation_refs set to the followed (non-seed) ids; date filled from S2.
    assert set(seed.citation_refs) == {_B, _C, _D}
    assert seed.publication_date == date(2023, 1, 1)

    # New ScoredURLs appended for each discovered paper, carrying its date.
    by_pid = {u.url: u for u in enriched[1:]}
    assert len(by_pid) == 3  # B, C, D
    dpaper = next(u for u in enriched[1:] if (_D in u.url))
    assert dpaper.publication_date == date(2019, 3, 3)
    assert dpaper.source == "citation_bfs"
    assert dpaper.strategy == CrawlStrategy.CRAWL


async def test_enrich_dedupes_against_existing_urls():
    client = _make_client()
    # Pre-seed a ScoredURL whose URL collides with discovered Paper B's S2 URL.
    existing = ScoredURL(
        url=f"https://www.semanticscholar.org/paper/{_B}",
        score=0.4,
        strategy=CrawlStrategy.CRAWL,
    )
    enriched = await enrich_with_citations(
        [_academic_seed(), existing],
        subtopic_question="q",
        depth=Depth.DEEP,
        source_classes=[SourceClass.ACADEMIC],
        client=client,
    )
    await client.aclose()

    urls = [u.url for u in enriched]
    # Paper B's URL appears exactly once (the pre-existing record, not duplicated).
    assert urls.count(f"https://www.semanticscholar.org/paper/{_B}") == 1


async def test_enrich_guard_skips_when_not_academic_and_deep():
    client = _make_client()
    seeds = [_academic_seed()]

    # depth NORMAL -> guard fails -> returned unchanged (no BFS, no enrichment).
    result = await enrich_with_citations(
        seeds,
        subtopic_question="q",
        depth=Depth.NORMAL,
        source_classes=[SourceClass.ACADEMIC],
        client=client,
    )
    await client.aclose()
    assert result == seeds
    assert result[0].citation_refs == []


async def test_enrich_skips_seed_with_no_paper_id():
    client = _make_client()
    web = ScoredURL(url="https://example.com/blog", score=0.6, strategy=CrawlStrategy.CRAWL)

    enriched = await enrich_with_citations(
        [web],
        subtopic_question="q",
        depth=Depth.DEEP,
        source_classes=[SourceClass.ACADEMIC],
        client=client,
    )
    await client.aclose()
    # No paper id extractable -> no BFS -> list unchanged (one record, no refs).
    assert len(enriched) == 1
    assert enriched[0].citation_refs == []


async def test_enrich_respects_relevance_scorer():
    client = _make_client()

    def scorer(context: str, _question: str) -> float:
        return 0.9 if "Paper B" in context else 0.1

    enriched = await enrich_with_citations(
        [_academic_seed()],
        subtopic_question="q",
        depth=Depth.DEEP,
        source_classes=[SourceClass.ACADEMIC],
        client=client,
        scorer=scorer,
    )
    await client.aclose()

    # Only B clears the gate; C dropped at hop1 and D (B->D) scores 0.1 -> dropped.
    followed = set(enriched[0].citation_refs)
    assert followed == {_B}
    assert len(enriched) == 2  # seed + Paper B


# ── acquire: end-to-end with injected runner + client ─────────────────────────
def _canned_triage_json() -> str:
    return json.dumps(
        [
            {
                "url": "https://arxiv.org/abs/2301.00001",
                "score": 0.8,
                "strategy": "crawl_url",
                "source": "arxiv",
            }
        ]
    )


async def test_acquire_runs_bfs_for_academic_deep():
    async def runner(_question: str) -> str:
        return _canned_triage_json()

    client = _make_client()
    result = await acquire(
        _subtopic([SourceClass.ACADEMIC]),
        _plan(Depth.DEEP),
        runner=runner,
        citation_client=client,
    )
    await client.aclose()

    seed = result[0]
    assert set(seed.citation_refs) == {_B, _C, _D}
    assert seed.publication_date == date(2023, 1, 1)
    assert len(result) == 4  # seed + B, C, D


async def test_acquire_skips_bfs_for_web_subtopic():
    async def runner(_question: str) -> str:
        return _canned_triage_json()

    # Even though depth is DEEP, a web-only subtopic must not trigger BFS.
    client = _make_client()
    result = await acquire(
        _subtopic([SourceClass.WEB]),
        _plan(Depth.DEEP),
        runner=runner,
        citation_client=client,
    )
    await client.aclose()

    assert len(result) == 1
    assert result[0].citation_refs == []


async def test_acquire_skips_bfs_for_normal_depth():
    async def runner(_question: str) -> str:
        return _canned_triage_json()

    result = await acquire(
        _subtopic([SourceClass.ACADEMIC]),
        _plan(Depth.NORMAL),
        runner=runner,
        # No client needed: guard fails before any client is used.
    )
    assert len(result) == 1
    assert result[0].citation_refs == []


async def test_acquire_runner_returning_list_is_parsed():
    async def runner(_question: str):
        return [
            {"url": "https://a.com", "score": 0.3, "strategy": "skip"},
        ]

    result = await acquire(
        _subtopic([SourceClass.WEB]),
        _plan(Depth.NORMAL),
        runner=runner,
    )
    assert result[0].url == "https://a.com"
    assert result[0].strategy == CrawlStrategy.SKIP


# ── acquire: resilience under tool timeouts (E0.S2) ───────────────────────────
async def test_acquire_retries_then_parses_after_prose_first_response():
    """First call returns timeout prose (no JSON); the JSON-only re-ask recovers."""
    calls: list[str] = []

    async def runner(question: str):
        calls.append(question)
        if len(calls) == 1:
            return "The discovery tool timed out, so I have no URLs to return."
        return _canned_triage_json()

    result = await acquire(
        _subtopic([SourceClass.WEB]),
        _plan(Depth.NORMAL),
        runner=runner,
    )
    assert len(calls) == 2  # one prose response, one successful re-ask
    assert result[0].url == "https://arxiv.org/abs/2301.00001"


async def test_acquire_degrades_to_empty_when_no_json():
    """Both attempts return pure prose → empty list, no crash."""
    async def runner(_question: str):
        return "All crawl tools timed out; nothing to triage."

    result = await acquire(
        _subtopic([SourceClass.WEB]),
        _plan(Depth.NORMAL),
        runner=runner,
    )
    assert result == []
