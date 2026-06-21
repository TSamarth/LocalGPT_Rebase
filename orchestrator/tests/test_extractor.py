"""
Offline unit tests for the Extractor agent (T3.5, FR4).

No live Ollama and no live MCP subprocess: ``build_agent`` constructs the
``LlmAgent`` without contacting Ollama (LiteLlm imports litellm lazily on the
first generate call), and a fake ``BaseToolset`` is injected so the real
``MCPToolset`` / ``mcp`` stdio path is never exercised.
"""
from __future__ import annotations

from datetime import date

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from app.agents.extractor import (
    STRATEGY_TOOL_NAMES,
    build_extractor,
    chunk_metadata_for,
)
from app.schemas import CrawlStrategy, ScoredURL


# ── Fake MCP toolset ──────────────────────────────────────────────────────────
class FakeToolset(BaseToolset):
    """Minimal in-memory ``BaseToolset`` so the agent builds with no stdio subprocess.

    ``LlmAgent.tools`` only accepts callables / ``BaseTool`` / ``BaseToolset``
    instances, so the fake must subclass ``BaseToolset`` and implement the single
    abstract ``get_tools`` method.
    """

    async def get_tools(self, readonly_context=None):  # noqa: D401 - test stub
        return []


def _scored(strategy: CrawlStrategy, pub: date | None = None) -> ScoredURL:
    return ScoredURL(
        url="https://example.com/paper",
        score=0.9,
        strategy=strategy,
        publication_date=pub,
    )


# ── build_extractor ───────────────────────────────────────────────────────────
def test_build_extractor_returns_tool_agent_with_injected_toolset():
    toolset = FakeToolset()
    agent = build_extractor(toolset=toolset)

    assert isinstance(agent, LlmAgent)
    # Tools are wired (the injected toolset is present)...
    assert agent.tools, "extractor must hold its crawl toolset"
    assert toolset in agent.tools
    # ...and as a tool agent it must NOT force an output schema (ADK constraint).
    assert agent.output_schema is None


def test_build_extractor_does_not_construct_real_toolset_when_injected(monkeypatch):
    """Injecting a toolset must bypass the real MCPToolset/stdio construction."""
    import app.agents.extractor as extractor_mod

    def _boom():
        raise AssertionError("_build_default_toolset must not run when a toolset is injected")

    monkeypatch.setattr(extractor_mod, "_build_default_toolset", _boom)
    agent = build_extractor(toolset=FakeToolset())
    assert isinstance(agent, LlmAgent)


def test_extractor_role_prompt_instructs_strategy_based_tool_choice():
    agent = build_extractor(toolset=FakeToolset())
    instruction = agent.instruction
    # Prompt must name each crawl tool so the model maps strategy -> tool.
    for tool_name in STRATEGY_TOOL_NAMES.values():
        assert tool_name in instruction
    assert "skip" in instruction.lower()


# ── chunk_metadata_for: date passthrough, no inference ────────────────────────
def test_chunk_metadata_includes_publication_date_when_present():
    scored = _scored(CrawlStrategy.CRAWL, pub=date(2021, 7, 15))
    meta = chunk_metadata_for(scored)

    assert meta["publication_date"] == "2021-07-15"
    assert meta["source_url"] == scored.url


def test_chunk_metadata_omits_publication_date_when_absent():
    scored = _scored(CrawlStrategy.CRAWL, pub=None)
    meta = chunk_metadata_for(scored)

    # Passthrough only: a missing date yields NO date key (no inference, no default).
    assert "publication_date" not in meta
    assert meta["source_url"] == scored.url


def test_chunk_metadata_does_not_infer_or_mutate_date():
    """Helper is pure passthrough: it never invents a date and never mutates input."""
    scored = _scored(CrawlStrategy.ADAPTIVE, pub=None)
    meta = chunk_metadata_for(scored)
    assert meta.get("publication_date") is None  # truly absent, not fabricated
    # Input artifact is untouched by the metadata build.
    assert scored.publication_date is None

    dated = _scored(CrawlStrategy.DEEP, pub=date(2019, 1, 1))
    meta2 = chunk_metadata_for(dated)
    # The emitted date equals the input date exactly — no shifting/normalising.
    assert meta2["publication_date"] == dated.publication_date.isoformat()


@pytest.mark.parametrize("strategy", list(STRATEGY_TOOL_NAMES))
def test_chunk_metadata_date_passthrough_across_strategies(strategy):
    """Date propagation is independent of the crawl strategy chosen."""
    meta = chunk_metadata_for(_scored(strategy, pub=date(2020, 3, 9)))
    assert meta["publication_date"] == "2020-03-09"
