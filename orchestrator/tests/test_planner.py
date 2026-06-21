"""
Planner agent tests (T3.3). The model is never called: ``plan`` is driven with an
injected async runner returning canned JSON, and the deterministic
``apply_depth_targets`` policy is checked directly. No Ollama, no network.
"""
from __future__ import annotations

import json

import pytest

from app.agents.planner import (
    OUTPUT_KEY,
    apply_depth_targets,
    build_planner,
    parse_plan,
    plan,
)
from app.config import config
from app.schemas import Depth, ResearchPlan, SourceClass, Subtopic


# ── fixtures / helpers ────────────────────────────────────────────────────────
def _plan_json(depth: str, n_subtopics: int = 2) -> str:
    """A representative model response: valid ResearchPlan JSON for `depth`."""
    subtopics = [
        {
            "id": f"s{i}",
            "question": f"sub-question {i} for a {depth} query?",
            # Deliberately a 'wrong'/placeholder value so we can prove the
            # deterministic depth policy overwrites whatever the model proposed.
            "target_evidence": 99,
            "source_classes": ["web", "academic"] if depth == "deep" else ["web"],
        }
        for i in range(1, n_subtopics + 1)
    ]
    return json.dumps(
        {
            "subtopics": subtopics,
            "depth": depth,
            "seed_urls": [],
            "est_iterations": 2 if depth == "deep" else 1,
        }
    )


def _runner_returning(payload):
    """Build an injectable async runner that yields a fixed payload."""

    async def _run(_normalized_query: str):
        return payload

    return _run


def _expected_target(depth: Depth) -> int:
    return {
        Depth.SHALLOW: config.TARGET_EVIDENCE_SHALLOW,
        Depth.NORMAL: config.TARGET_EVIDENCE_NORMAL,
        Depth.DEEP: config.TARGET_EVIDENCE_DEEP,
    }[depth]


# ── build_planner ─────────────────────────────────────────────────────────────
def test_build_planner_is_reasoning_only_with_schema():
    agent = build_planner()
    assert agent.output_schema is ResearchPlan
    assert agent.output_key == OUTPUT_KEY
    assert isinstance(agent.instruction, str) and agent.instruction.strip()
    # Reasoning-only: no tools (least-privilege, architecture.md §1/§3.3).
    assert not agent.tools


def test_build_planner_prompt_mentions_core_responsibilities():
    instruction = build_planner().instruction.lower()
    for token in ("subtopic", "depth", "shallow", "normal", "deep", "target_evidence"):
        assert token in instruction


def test_build_planner_accepts_injected_model_without_ollama():
    # A plain model string is accepted by ADK and bypasses build_model()/LiteLlm,
    # proving construction never reaches out to Ollama.
    agent = build_planner(model="stub-model")
    assert agent.model == "stub-model"


# ── apply_depth_targets (deterministic policy) ────────────────────────────────
@pytest.mark.parametrize(
    "depth, expected",
    [
        (Depth.SHALLOW, config.TARGET_EVIDENCE_SHALLOW),
        (Depth.NORMAL, config.TARGET_EVIDENCE_NORMAL),
        (Depth.DEEP, config.TARGET_EVIDENCE_DEEP),
    ],
)
def test_apply_depth_targets_maps_each_depth(depth, expected):
    src = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="q1", target_evidence=1),
            Subtopic(id="s2", question="q2", target_evidence=7,
                     source_classes=[SourceClass.ACADEMIC]),
        ],
        depth=depth,
    )
    out = apply_depth_targets(src)
    assert {s.target_evidence for s in out.subtopics} == {expected}


def test_apply_depth_targets_concrete_values_2_3_5():
    """Spec acceptance: shallow/normal/deep -> 2/3/5 across subtopics."""
    assert config.TARGET_EVIDENCE_SHALLOW == 2
    assert config.TARGET_EVIDENCE_NORMAL == 3
    assert config.TARGET_EVIDENCE_DEEP == 5
    for depth, expected in (
        (Depth.SHALLOW, 2),
        (Depth.NORMAL, 3),
        (Depth.DEEP, 5),
    ):
        p = ResearchPlan(
            subtopics=[Subtopic(id="s1", question="q", target_evidence=1)],
            depth=depth,
        )
        assert apply_depth_targets(p).subtopics[0].target_evidence == expected


def test_apply_depth_targets_is_pure():
    src = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="q", target_evidence=4)],
        depth=Depth.DEEP,
    )
    out = apply_depth_targets(src)
    assert out is not src
    assert src.subtopics[0].target_evidence == 4  # input untouched
    assert out.subtopics[0].target_evidence == config.TARGET_EVIDENCE_DEEP


# ── parse_plan ────────────────────────────────────────────────────────────────
def test_parse_plan_accepts_str_dict_and_model():
    from_str = parse_plan(_plan_json("normal"))
    from_dict = parse_plan(json.loads(_plan_json("normal")))
    from_model = parse_plan(ResearchPlan.model_validate_json(_plan_json("normal")))
    assert from_str.depth == from_dict.depth == from_model.depth == Depth.NORMAL
    # Normalization is applied regardless of input form.
    for p in (from_str, from_dict, from_model):
        assert all(
            s.target_evidence == config.TARGET_EVIDENCE_NORMAL for s in p.subtopics
        )


# ── plan() end-to-end with injected runner (no model) ─────────────────────────
async def test_plan_parses_model_json_and_normalizes_targets():
    result = await plan("explain X", runner=_runner_returning(_plan_json("deep", 3)))
    assert isinstance(result, ResearchPlan)
    assert result.depth == Depth.DEEP
    assert len(result.subtopics) == 3
    # Model's placeholder (99) overwritten by depth policy.
    assert all(s.target_evidence == config.TARGET_EVIDENCE_DEEP for s in result.subtopics)


async def test_plan_accepts_dict_payload_from_runner():
    payload = json.loads(_plan_json("shallow"))
    result = await plan("what is Y?", runner=_runner_returning(payload))
    assert result.depth == Depth.SHALLOW
    assert all(s.target_evidence == config.TARGET_EVIDENCE_SHALLOW for s in result.subtopics)


async def test_plan_depth_varies_across_sample_queries():
    """Different model responses yield different depths, each correctly normalized."""
    samples = {
        "what year did the Eiffel Tower open?": "shallow",
        "compare two popular web frameworks": "normal",
        "survey the state of the art in protein folding research": "deep",
    }
    seen_depths = set()
    for query, depth in samples.items():
        result = await plan(query, runner=_runner_returning(_plan_json(depth)))
        assert result.depth == Depth(depth)
        assert all(
            s.target_evidence == _expected_target(Depth(depth)) for s in result.subtopics
        )
        seen_depths.add(result.depth)
    assert seen_depths == {Depth.SHALLOW, Depth.NORMAL, Depth.DEEP}


async def test_plan_passes_query_to_runner():
    captured = {}

    async def _capturing_runner(q: str):
        captured["query"] = q
        return _plan_json("normal")

    await plan("  normalized query text  ", runner=_capturing_runner)
    assert captured["query"] == "  normalized query text  "
