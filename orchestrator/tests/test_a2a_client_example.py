"""E4.T4 — offline proof that the RemoteA2aAgent consumption example works.

Proves the agent built by ``app.a2a_client_example.build_remote_research_agent``
actually consumes the pipeline over A2A, fully OFFLINE and in-process: it reuses
the T1b harness (``tests/test_a2a_hitl_gate._build_stub_server``) to stand up the
DB-backed stub server behind the SAME production A2A wiring, then drives the
example's remote agent through an ``httpx.ASGITransport`` (no real port, no
model). The example consuming the pipeline end-to-end (CP1 pause → resume → CP2
pause → resume → terminal ``ClaimLedger``) IS the gate.

The construction/import being offline-safe is covered by importing the example
module at the top of this file with no network I/O.
"""
from __future__ import annotations

import warnings

import httpx
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types
from test_a2a_hitl_gate import _build_stub_server  # reuse the T1b harness
from test_workflow import RAW_QUERY  # noqa: E402

from app.a2a_client_example import (
    AGENT_CARD_URL,
    REMOTE_AGENT_NAME,
    build_remote_research_agent,
)
from app.schemas import ClaimLedger, ClaimStatus, Depth, ResearchPlan, Subtopic
from app.server import APP_NAME

warnings.filterwarnings("ignore", message=r".*ResumabilityConfig.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*\[EXPERIMENTAL\].*", category=UserWarning)


def test_example_card_url_matches_served_route():
    """The example's card URL is exactly the well-known path the server mounts."""
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

    assert AGENT_CARD_URL.endswith(f"/a2a/{APP_NAME}{AGENT_CARD_WELL_KNOWN_PATH}")
    assert REMOTE_AGENT_NAME.isidentifier()


async def test_example_consumes_pipeline_over_a2a(tmp_path):
    """THE GATE: the example's RemoteA2aAgent drives a full run over A2A offline."""
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    server_app, db_service = _build_stub_server(
        tmp_path, clarifier_calls=clarifier_calls, planner_calls=planner_calls
    )

    # In-process transport: the whole A2A round-trip flows through the server app
    # object — no real port. The example factory accepts this injected client so
    # the exact same construction used against a live server is exercised here.
    transport = httpx.ASGITransport(app=server_app)
    httpx_client = httpx.AsyncClient(transport=transport, base_url="http://localhost:8001")
    remote = build_remote_research_agent(httpx_client=httpx_client)
    assert remote.name == REMOTE_AGENT_NAME

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

    approved_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )

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
                else:
                    cp2_interrupt_id = iid
                    pending = (iid, {"result": ""})
        if pending is None:
            break
        message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    # ── The example consumed the pipeline over A2A, HITL and all ─────────────
    assert cp1_interrupt_id is not None, "CP1 never surfaced via the example agent"
    assert cp2_interrupt_id is not None, "CP2 never surfaced after the A2A resume"
    assert a2a_context_id is not None

    # The resumed run reached a terminal ClaimLedger on the server session.
    server_session = await db_service.get_session(
        app_name=APP_NAME,
        user_id=f"A2A_USER_{a2a_context_id}",
        session_id=a2a_context_id,
    )
    assert server_session is not None
    terminal_output = None
    for ev in server_session.events:
        if getattr(ev, "output", None) is not None:
            terminal_output = ev.output
    assert terminal_output is not None, "example consumption did not reach a terminal output"
    ledger = ClaimLedger.model_validate(terminal_output)
    assert {c.id for c in ledger.claims} == {"c1"}
    assert ledger.claims[0].status == ClaimStatus.KEPT

    await httpx_client.aclose()
