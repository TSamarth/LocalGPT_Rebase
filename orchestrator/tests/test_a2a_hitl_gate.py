"""E4.T1b — HARD GATE: single RequestInput pause→resume over the A2A protocol.

De-risks the highest-risk E4 decision (full HITL over A2A) before any build
task. It drives CP1 (the plan-approval ``RequestInput``) to an A2A
``input-required`` task via ``RemoteA2aAgent(use_legacy=False)`` against a
DB-backed server, answers it over the wire with the approved ``ResearchPlan``,
and proves:

  (a) the workflow RESUMES across the A2A boundary to a terminal result, and
  (b) the clarifier/planner node BODIES do NOT re-run on resume — proven by
      body-side call counters that stay ``[1]`` (would be ``[1, 1]`` if a
      completed node re-executed). That counter equality IS the gate.

Everything runs OFFLINE and in-process: the server workflow is built from the
counting ``@node`` stubs (no Ollama), behind a ``DatabaseSessionService`` over a
tmp sqlite DB (so ``invocation_id`` resume is genuine across the A2A turns), and
the ``RemoteA2aAgent`` client talks to the server through an
``httpx.ASGITransport`` — no real port is bound. The A2A routes are mounted by
the SAME production helper the real server uses (``app.server.mount_a2a_routes``).

How the A2A input-required round-trip works (verified against ADK 2.3.0):
  * The server's CP1 ``RequestInput`` surfaces as a *real* long-running function
    call (``adk_request_input``); the executor publishes it as an
    ``input-required`` task whose status message carries that function-call data
    part (marked long-running).
  * The client (``convert_a2a_task_to_event`` →
    ``_create_mock_function_call_for_required_user_input``) preserves the real
    function call (it does NOT substitute a mock, because the long-running id is
    present), so on the client side the event still answers
    ``has_request_input_function_call`` with the SAME ``interrupt_id``.
  * We answer with ``create_request_input_response(interrupt_id, plan_dict)``;
    because the call name is ``adk_request_input`` (not the mock name), the
    client serialises the structured reply verbatim as a function-response data
    part on the same A2A task/context.
  * Server-side the runner auto-resolves the ``invocation_id`` from that function
    response (``Runner._resolve_invocation_id``) and resumes the paused
    workflow — completed nodes are checkpoint-skipped.

CP2 (the unconditional draft-approval ``RequestInput``) also fires after the
Writer and is answered the same way (empty = approve) so the run reaches its
terminal ledger. CP3 never fires (NORMAL depth). The terminal ``ClaimLedger`` is
the workflow's ``event.output``, which ADK's A2A surface does not project as an
artifact — so the terminal RESULT is verified on the persisted server session
(the round-trip + resume themselves are proven client-side by the pauses and the
node-skip counters).
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import httpx
from fastapi import FastAPI
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types

# Reuse the offline node stubs + body counters from the in-process resume gate
# (E2.S1 T5) verbatim — the same counters that prove node-skip there prove it
# across the A2A boundary here.
from test_workflow import (  # noqa: E402
    RAW_QUERY,
    _acquirer_stub,
    _counting_clarifier_stub,
    _counting_planner_stub,
    _extractor_stub,
    _kept_claim,
    _verifier_stub_from_passes,
    _writer_stub,
)

from app.runner import build_runner
from app.schemas import ClaimLedger, ClaimStatus, Depth, ResearchPlan, Subtopic
from app.server import _AGENT_CARD_PATH, APP_NAME, mount_a2a_routes
from app.workflow import build_research_workflow

# The ResumabilityConfig + A2A-experimental UserWarnings are expected and noisy.
warnings.filterwarnings("ignore", message=r".*ResumabilityConfig.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*\[EXPERIMENTAL\].*", category=UserWarning)


def _build_stub_server(tmp_path, *, clarifier_calls, planner_calls):
    """Stand up the offline in-process A2A server over a stub workflow.

    Returns ``(server_app, db_service)``: the FastAPI app carrying the A2A
    routes (mounted by the production ``mount_a2a_routes``) and the DB-backed
    session service (so the terminal result can be verified post-hoc).
    """
    workflow = build_research_workflow(
        clarifier_node=_counting_clarifier_stub(clarifier_calls),
        planner_node=_counting_planner_stub(planner_calls),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        # One pass yields a single KEPT claim → target met → loop exits fast.
        verifier_node=_verifier_stub_from_passes(
            [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
        ),
        writer_node=_writer_stub(),
    )
    stub_app = App(
        name=APP_NAME,
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    db_url = f"sqlite+aiosqlite:///{tmp_path}/a2a_gate.db"
    db_service = DatabaseSessionService(db_url=db_url)
    stub_runner = build_runner(stub_app, session_service=db_service)

    from a2a.types import AgentCard

    agent_card = AgentCard(
        **json.loads(Path(_AGENT_CARD_PATH).read_text(encoding="utf-8"))
    )
    server_app = FastAPI()
    mount_a2a_routes(
        server_app, runner=stub_runner, agent_card=agent_card, app_name=APP_NAME
    )
    return server_app, db_service


async def test_cp1_request_input_pause_resume_over_a2a(tmp_path):
    """THE GATE: CP1 RequestInput pause→resume over A2A; nodes skip on resume."""
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    server_app, db_service = _build_stub_server(
        tmp_path, clarifier_calls=clarifier_calls, planner_calls=planner_calls
    )

    # In-process A2A client: ASGITransport routes the whole round-trip through
    # the server app object — no real port. ``use_legacy=False`` activates the
    # new A2aAgentExecutor (the A2A-extension executor) so RequestInput surfaces
    # as an input-required task.
    transport = httpx.ASGITransport(app=server_app)
    httpx_client = httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    )
    remote = RemoteA2aAgent(
        name="localgpt_research_remote",
        agent_card="http://localhost:8001/a2a/localgpt_research/.well-known/agent-card.json",
        httpx_client=httpx_client,
        use_legacy=False,
    )
    client_runner = Runner(
        app=App(
            name="a2a_client",
            root_agent=remote,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=InMemorySessionService(),
    )
    client_session = await client_runner.session_service.create_session(
        app_name="a2a_client", user_id="client-user"
    )

    # CP1 reply: a NORMAL 1-subtopic plan (target_evidence=1) so the loop meets
    # its target in one pass and NO CP3 fires — the gate stays focused on CP1.
    approved_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )

    # Drive the run over A2A, answering each RequestInput pause and resuming on
    # the same A2A task/context. CP1 (plan) is answered with the approved plan;
    # CP2 (draft) is answered with an empty approve. A small turn cap guards
    # against a non-terminating loop (the offline stubs terminate in 2 hops).
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    a2a_context_id = None
    cp1_seen = False
    cp1_interrupt_id = None
    cp2_interrupt_id = None
    for _ in range(8):
        pending = None  # (interrupt_id, reply_payload)
        async for event in client_runner.run_async(
            user_id="client-user",
            session_id=client_session.id,
            new_message=message,
        ):
            meta = event.custom_metadata or {}
            if meta.get("a2a:context_id"):
                a2a_context_id = meta["a2a:context_id"]
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                if not cp1_seen:
                    cp1_seen = True
                    cp1_interrupt_id = iid
                    pending = (iid, approved_plan.model_dump(mode="json"))
                else:  # CP2 draft-approval — empty reply approves unchanged.
                    cp2_interrupt_id = iid
                    pending = (iid, {"result": ""})
        if pending is None:
            break  # no further pause → the run reached a terminal state
        message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    # ── The round-trip happened over A2A ────────────────────────────────────
    assert cp1_interrupt_id is not None, "CP1 never surfaced as an A2A input-required task"
    assert cp2_interrupt_id is not None, "CP2 never surfaced after the A2A resume"
    assert a2a_context_id is not None

    # ── THE GATE: clarifier/planner bodies were checkpoint-skipped on resume ─
    # Each ran exactly once (to reach CP1); they are STILL [1] after the A2A
    # resume drove the loop to completion. [1, 1] would mean a completed node
    # re-executed — i.e. the gate would FAIL.
    assert clarifier_calls == [1], f"GATE FAIL: clarifier re-ran on A2A resume: {clarifier_calls}"
    assert planner_calls == [1], f"GATE FAIL: planner re-ran on A2A resume: {planner_calls}"

    # ── (a) the resumed run reached a terminal ClaimLedger ───────────────────
    # The terminal output is the workflow's ``event.output``; ADK's A2A surface
    # does not project it as an artifact, so it is verified on the persisted
    # server session (the resume itself is proven by the node-skip counters and
    # the CP1→CP2 pause sequence above).
    server_session = await db_service.get_session(
        app_name=APP_NAME,
        user_id=f"A2A_USER_{a2a_context_id}",
        session_id=a2a_context_id,
    )
    assert server_session is not None, "A2A server session was not persisted"
    terminal_output = None
    for ev in server_session.events:
        if getattr(ev, "output", None) is not None:
            terminal_output = ev.output
    assert terminal_output is not None, "resume did not produce a terminal output"
    ledger = ClaimLedger.model_validate(terminal_output)
    assert {c.id for c in ledger.claims} == {"c1"}
    assert ledger.claims[0].status == ClaimStatus.KEPT
    assert ledger.kept_count("s1") == 1

    # The approved plan + draft persisted across the A2A resume boundary too.
    assert server_session.state.get("v2_plan", {}).get("depth") == "normal"
    assert "v2_draft" in server_session.state

    await httpx_client.aclose()
