"""
Unit tests for the Clarifier agent (T3.2, FR1).

No live model: the ADK ``LlmAgent`` is built offline (``LiteLlm`` imports
``litellm`` lazily) and every model invocation is stubbed via the injectable
``runner`` argument or the pure JSON parser. Nothing here touches Ollama.
"""
from __future__ import annotations

import json

import pytest
from google.adk.agents import LlmAgent

from app.agents.clarifier import (
    AGENT_NAME,
    OUTPUT_KEY,
    build_clarifier,
    clarify,
    parse_clarify_result,
)
from app.schemas import ClarifyResult

# ── Representative model outputs ──────────────────────────────────────────────
CLEAR_JSON = json.dumps(
    {
        "status": "clear",
        "normalized_query": "Compare the battery life of the iPhone 15 Pro and Samsung Galaxy S24 Ultra in 2024.",
        "questions": [],
    }
)

NEEDS_INPUT_JSON = json.dumps(
    {
        "status": "needs_input",
        "normalized_query": "Research the best database.",
        "questions": [
            "Best for what workload (OLTP, analytics, vector search)?",
            "What scale and budget constraints apply?",
            "Any required ecosystem or language compatibility?",
        ],
    }
)


# ── build_clarifier ───────────────────────────────────────────────────────────
def test_build_clarifier_returns_llm_agent_with_schema():
    agent = build_clarifier()
    assert isinstance(agent, LlmAgent)
    assert agent.name == AGENT_NAME
    assert agent.output_schema is ClarifyResult
    assert agent.output_key == OUTPUT_KEY


def test_build_clarifier_is_reasoning_only_no_tools():
    agent = build_clarifier()
    # Reasoning-only agents must carry no tools (ADK forbids tools + output_schema).
    assert not agent.tools


def test_build_clarifier_instruction_is_strong_and_nonempty():
    agent = build_clarifier()
    instruction = agent.instruction
    assert isinstance(instruction, str) and instruction.strip()
    lowered = instruction.lower()
    # The prompt must name both detection duties and the structured contract.
    assert "ambiguous" in lowered
    assert "constraint" in lowered
    assert "needs_input" in lowered
    assert "clear" in lowered
    assert "normalized_query" in lowered


# ── parse_clarify_result: happy paths ─────────────────────────────────────────
def test_parse_clear_result():
    result = parse_clarify_result(CLEAR_JSON)
    assert isinstance(result, ClarifyResult)
    assert result.status == "clear"
    assert result.normalized_query.startswith("Compare the battery life")
    assert result.questions == []


def test_parse_needs_input_populates_questions():
    result = parse_clarify_result(NEEDS_INPUT_JSON)
    assert result.status == "needs_input"
    assert len(result.questions) == 3
    assert all(q for q in result.questions)


def test_parse_accepts_dict_input():
    result = parse_clarify_result(
        {"status": "clear", "normalized_query": "Q", "questions": []}
    )
    assert result.status == "clear"
    assert result.normalized_query == "Q"


def test_parse_is_idempotent_on_clarifyresult():
    original = ClarifyResult(status="clear", normalized_query="Q", questions=[])
    assert parse_clarify_result(original) is not None
    assert parse_clarify_result(original).status == "clear"


# ── parse_clarify_result: leniency & normalisation ────────────────────────────
def test_parse_strips_code_fences_and_prose():
    raw = (
        "Here is my analysis:\n```json\n"
        + NEEDS_INPUT_JSON
        + "\n```\nHope that helps!"
    )
    result = parse_clarify_result(raw)
    assert result.status == "needs_input"
    assert len(result.questions) == 3


def test_parse_normalises_status_case_and_whitespace():
    result = parse_clarify_result(
        {"status": "  CLEAR ", "normalized_query": "Q", "questions": []}
    )
    assert result.status == "clear"


def test_parse_drops_questions_when_clear():
    # Model contradiction: status clear but stray questions → questions dropped.
    result = parse_clarify_result(
        {"status": "clear", "normalized_query": "Q", "questions": ["stray?"]}
    )
    assert result.status == "clear"
    assert result.questions == []


def test_parse_filters_blank_questions():
    result = parse_clarify_result(
        {
            "status": "needs_input",
            "normalized_query": "Q",
            "questions": ["real?", "   ", ""],
        }
    )
    assert result.questions == ["real?"]


# ── parse_clarify_result: error paths ─────────────────────────────────────────
def test_parse_rejects_invalid_json():
    with pytest.raises(ValueError):
        parse_clarify_result("not json at all")


def test_parse_rejects_unknown_status():
    with pytest.raises(ValueError):
        parse_clarify_result(
            {"status": "maybe", "normalized_query": "Q", "questions": []}
        )


def test_parse_rejects_needs_input_without_questions():
    with pytest.raises(ValueError):
        parse_clarify_result(
            {"status": "needs_input", "normalized_query": "Q", "questions": []}
        )


def test_parse_rejects_non_object_json():
    with pytest.raises(ValueError):
        parse_clarify_result("[1, 2, 3]")


# ── clarify(): injectable runner (no Ollama) ──────────────────────────────────
@pytest.mark.asyncio
async def test_clarify_with_sync_runner_clear():
    result = await clarify("compare iphone 15 vs s24 battery", runner=lambda q: CLEAR_JSON)
    assert result.status == "clear"
    assert result.questions == []


@pytest.mark.asyncio
async def test_clarify_with_async_runner_needs_input():
    async def fake_runner(query: str) -> str:
        assert query  # query is threaded through
        return NEEDS_INPUT_JSON

    result = await clarify("best database?", runner=fake_runner)
    assert result.status == "needs_input"
    assert result.questions


@pytest.mark.asyncio
async def test_clarify_passes_query_to_runner():
    seen = {}

    def capture(query: str) -> str:
        seen["query"] = query
        return CLEAR_JSON

    await clarify("  Tell me about transformers  ", runner=capture)
    assert seen["query"] == "  Tell me about transformers  "


@pytest.mark.asyncio
async def test_clarify_rejects_empty_query():
    with pytest.raises(ValueError):
        await clarify("   ", runner=lambda q: CLEAR_JSON)


@pytest.mark.asyncio
async def test_clarify_propagates_parser_errors():
    with pytest.raises(ValueError):
        await clarify("x", runner=lambda q: "garbage")
