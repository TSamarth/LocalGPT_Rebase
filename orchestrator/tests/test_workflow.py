"""
Research workflow tests (E2.S1). The slice runs entirely offline: clarifier and
planner are canned ``@node`` stubs injected via the DI seam, so no live model, no
Ollama, no network. The workflow is driven through a real ADK ``InMemoryRunner``
over a resumable ``App`` — exercising the genuine dynamic-workflow engine, not a
hand-rolled harness.

Case (a) is the story gate for T1+T3: START→CP1→resume returns an approved
``ResearchPlan`` with depth-normalized ``target_evidence``. Case (b) is the T4
gate: an *edited* plan supplied at CP1 round-trips through the checkpoint as the
final approved plan. The T6 test proves the optional ``SessionStore`` write-through
lands ``plan.json`` on disk during the run.

CP1 is now a real ``RequestInput`` checkpoint, so every case must *resume* the run
to reach the terminal plan — :func:`_drive_to_cp1_and_resume` captures the
``interrupt_id`` / ``invocation_id`` at the pause, builds the FunctionResponse
reply, and runs the runner a second time with it.
"""
from __future__ import annotations

import warnings

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import InMemoryRunner
from google.adk.workflow import node
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types

from app.config import config
from app.schemas import ClarifyResult, Depth, ResearchPlan, Subtopic
from app.session import SessionStore
from app.workflow import build_research_workflow

RAW_QUERY = "  tell me about rust async runtimes  "

# The ResumabilityConfig EXPERIMENTAL warning is expected and noisy — filter it.
warnings.filterwarnings(
    "ignore",
    message=r".*ResumabilityConfig.*",
    category=UserWarning,
)


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


def _build_app(workflow) -> App:
    return App(
        name="test_research",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


async def _drive_to_cp1_and_resume(workflow, *, approved_plan: ResearchPlan) -> ResearchPlan:
    """Drive the slice to the CP1 pause, then resume with ``approved_plan``.

    The CP1 ``_cp1_checkpoint`` node yields ``RequestInput`` and interrupts. We:
      1. run the runner, draining events until the ``adk_request_input``
         function-call event appears — capturing its ``interrupt_id`` and the
         ``invocation_id`` ADK assigned to this run (both ride on that event);
      2. build the resume reply with ``create_request_input_response`` (a
         FunctionResponse ``Part`` keyed by ``interrupt_id``, carrying the
         approved plan as a dict), wrap it in a ``user`` ``Content``;
      3. run the runner a second time with ``invocation_id=<captured>`` and that
         reply as ``new_message`` — ADK validates it against the node's
         ``response_schema`` and feeds it back as the node's output, so the
         parent ``research`` node finishes and emits the terminal plan.

    Returns the terminal ``ResearchPlan`` (parsed from ``Event.output``, which
    ADK serializes via ``model_dump()`` — hence the ``model_validate``).
    """
    app = _build_app(workflow)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    # ── Phase 1: run to the CP1 pause, capturing interrupt_id + invocation_id ──
    interrupt_id = None
    invocation_id = None
    async for event in runner.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            interrupt_id = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id

    assert interrupt_id is not None, "workflow did not pause at the CP1 RequestInput"
    assert invocation_id is not None

    # ── Phase 2: resume with the human-approved plan as a FunctionResponse ──────
    reply_part = create_request_input_response(
        interrupt_id, approved_plan.model_dump(mode="json")
    )
    reply = types.Content(role="user", parts=[reply_part])

    final_output = None
    async for event in runner.run_async(
        user_id="test-user",
        session_id=session.id,
        invocation_id=invocation_id,
        new_message=reply,
    ):
        if event.output is not None:
            final_output = event.output
    return ResearchPlan.model_validate(final_output)


# ── Case (a): slice runs START→CP1→resume(approve) and returns the plan ────────
async def test_slice_returns_approved_research_plan():
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
    )
    # Approve the plan unchanged at CP1: resume with the same shape the planner
    # stub produced. The slice's ``parse_plan`` re-applies the depth policy to
    # the resumed plan, so the terminal target_evidence is the normalized value.
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=config.TARGET_EVIDENCE_DEEP),
            Subtopic(id="s2", question="angle 2", target_evidence=config.TARGET_EVIDENCE_DEEP),
        ],
        depth=Depth.DEEP,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert isinstance(result, ResearchPlan)
    assert len(result.subtopics) == 2
    # Depth policy applied in the slice: deep → TARGET_EVIDENCE_DEEP.
    assert result.depth == Depth.DEEP
    assert all(
        s.target_evidence == config.TARGET_EVIDENCE_DEEP for s in result.subtopics
    )


# ── Case (b) (T4 gate): an EDITED plan supplied at CP1 round-trips to final ─────
async def test_cp1_edited_plan_round_trips():
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
    )
    # The human edits the plan at CP1: drop a subtopic and reword the survivor.
    # This differs from the planner stub (2 subtopics), so a passthrough would
    # fail — proving the human response (not the planner output) is what wins.
    edited = ResearchPlan(
        subtopics=[
            Subtopic(
                id="s1",
                question="EDITED: only rust async runtime fairness",
                target_evidence=config.TARGET_EVIDENCE_DEEP,
            ),
        ],
        depth=Depth.DEEP,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=edited)

    assert isinstance(result, ResearchPlan)
    assert len(result.subtopics) == 1
    assert result.subtopics[0].id == "s1"
    assert result.subtopics[0].question == "EDITED: only rust async runtime fairness"
    assert result.depth == Depth.DEEP
    assert result.subtopics[0].target_evidence == config.TARGET_EVIDENCE_DEEP


# ── T6: SessionStore write-through lands plan.json during the slice run ─────────
async def test_store_write_through_persists_plan():
    store = SessionStore.create(RAW_QUERY)
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
        store=store,
    )
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=config.TARGET_EVIDENCE_DEEP),
            Subtopic(id="s2", question="angle 2", target_evidence=config.TARGET_EVIDENCE_DEEP),
        ],
        depth=Depth.DEEP,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    plan_path = store.dir / SessionStore.PLAN_FILE
    assert plan_path.exists(), "T6 write-through did not persist plan.json"
    persisted = store.load_plan()
    assert isinstance(persisted, ResearchPlan)
    # Persisted plan is the planner's slice output (depth-normalized), written
    # before the CP1 pause — so it carries the normalized target_evidence.
    assert len(persisted.subtopics) == 2
    assert all(
        s.target_evidence == config.TARGET_EVIDENCE_DEEP for s in persisted.subtopics
    )
