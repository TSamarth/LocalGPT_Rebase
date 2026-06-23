"""
Research workflow tests (E2.S1). The slice runs entirely offline: clarifier and
planner are canned ``@node`` stubs injected via the DI seam, so no live model, no
Ollama, no network. The workflow is driven through a real ADK ``InMemoryRunner``
over a resumable ``App`` — exercising the genuine dynamic-workflow engine, not a
hand-rolled harness.

Case (a) is the story gate for T1+T3: START→CP1 returns an approved
``ResearchPlan`` with depth-normalized ``target_evidence``. (Cases (b)/(c) land
with T4/T5.)
"""
from __future__ import annotations

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import InMemoryRunner
from google.adk.workflow import node
from google.genai import types

from app.config import config
from app.schemas import ClarifyResult, Depth, ResearchPlan, Subtopic
from app.workflow import build_research_workflow

RAW_QUERY = "  tell me about rust async runtimes  "


def _clarifier_stub() -> object:
    """Canned clarifier node: echoes a normalized query, no model call."""

    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(status="clear", normalized_query="normalized: " + str(node_input).strip())

    return _stub


def _planner_stub() -> object:
    """Canned planner node: a deep-depth plan with a placeholder target_evidence
    (99) so we can prove ``apply_depth_targets`` normalizes it in the slice."""

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[
                Subtopic(id="s1", question=f"angle 1 of {node_input}", target_evidence=99),
                Subtopic(id="s2", question=f"angle 2 of {node_input}", target_evidence=99),
            ],
            depth=Depth.DEEP,
        )

    return _stub


async def _run_to_final_output(workflow) -> ResearchPlan:
    """Drive a workflow through InMemoryRunner and return the terminal plan.

    The final ``research`` node's return value surfaces on ``Event.output`` (ADK
    carries dynamic-node output there). ADK serializes a node's ``BaseModel``
    return via ``model_dump()``, so the terminal output arrives as a dict — we
    keep the last non-None output and parse it back into a ``ResearchPlan``.
    """
    app = App(
        name="test_research",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    final_output = None
    async for event in runner.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if event.output is not None:
            final_output = event.output
    return ResearchPlan.model_validate(final_output)


# ── Case (a): slice runs START→CP1 and returns an approved ResearchPlan ────────
async def test_slice_returns_approved_research_plan():
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
    )
    result = await _run_to_final_output(workflow)

    assert isinstance(result, ResearchPlan)
    assert len(result.subtopics) == 2
    # Depth policy applied in the slice: deep → TARGET_EVIDENCE_DEEP, overwriting
    # the planner stub's placeholder (99).
    assert result.depth == Depth.DEEP
    assert all(
        s.target_evidence == config.TARGET_EVIDENCE_DEEP for s in result.subtopics
    )
