"""E4.T5 — A2A round-trip ACCEPTANCE test (epic gate).

End-to-end, fully OFFLINE (canned ``@node`` stubs, ``httpx.ASGITransport``, no
real model or port) acceptance of BOTH wire surfaces the unified server exposes,
plus the key acceptance: PARITY of the ledger/draft produced over the wire with
the in-process workflow result for the SAME stubs.

Three things are proven:

  (A) **A2A protocol round-trip with full HITL** —
      ``RemoteA2aAgent(use_legacy=False)`` drives a run; CP pauses surface over
      A2A; the client answers them and the workflow resumes to a terminal
      ``ClaimLedger``. A NORMAL plan exercises CP1 (plan) + CP2 (draft); a DEEP
      plan additionally exercises the deep-only CP3 (sources) so the FULL
      checkpoint sequence is shown to round-trip over the A2A protocol. On resume
      the clarifier/planner BODIES do not re-run — proven by the body-counter
      technique from the T1b gate (counters stay ``[1]``).

  (B) **REST resume-by-invocation_id** — ``app.cli.run_cli`` drives a full run
      over ``/run_sse``, pausing at CP1/CP2 and resuming by ``invocation_id``
      (the variant ``test_cli.py`` established) to a terminal ledger + draft.

  (C) **PARITY (the acceptance)** — the ledger AND the byte-exact markdown draft
      produced over A2A and over REST each equal the in-process workflow result
      for the same stubs/approved plan. The draft is deterministic
      (``render_report`` is pure Python over the plan + ledger), so equality is
      asserted verbatim.

Where the terminal result lands on each surface (verified):
  * **A2A** — ADK's ``from_adk_event`` converter projects only
    ``event.content.parts`` as artifacts, so the workflow's terminal
    ``event.output`` (the ``ClaimLedger``) is NOT carried to the A2A client as
    content. The terminal ledger is therefore read from the persisted server
    session (``event.output`` on the DB-backed session) and the approved draft
    from that session's ``v2_draft`` state — NOT from the A2A response.
  * **REST** — the terminal ledger arrives as ``event.output`` on the SSE stream
    and the draft via the ``v2_draft`` ``state_delta`` (as ``run_cli`` returns).

The live wiring reuses the production harnesses verbatim — the T1b A2A stub
server (``test_a2a_hitl_gate._build_stub_server``, mounted by the SAME
``app.server.mount_a2a_routes``) and the T3 REST stub server
(``test_cli._build_stub_server`` via the production ``get_fast_api_app``) — so no
stub logic is duplicated here.
"""
from __future__ import annotations

import warnings

import httpx
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types

# Reuse the production harnesses verbatim — no stub logic is re-implemented here.
from test_a2a_hitl_gate import _build_stub_server as _build_a2a_stub_server
from test_cli import _build_stub_server as _build_rest_stub_server
from test_cli import _normal_workflow
from test_workflow import RAW_QUERY, _drive_through_checkpoints

from app.cli import run_cli
from app.schemas import ClaimLedger, ClaimStatus, Depth, ResearchPlan, Subtopic
from app.server import APP_NAME

warnings.filterwarnings("ignore", message=r".*ResumabilityConfig.*", category=UserWarning)
warnings.filterwarnings("ignore", message=r".*\[EXPERIMENTAL\].*", category=UserWarning)

# The SAME approved plan answered at CP1 on every surface, so the loop, ledger
# and (deterministic) draft are identical across the wire and in-process. The
# subtopic question text is what ``render_report`` renders into the Coverage
# section, so it must match the REST planner's plan (``_normal_workflow`` uses
# ``_single_subtopic_planner_stub(1, Depth.NORMAL)`` → id ``s1`` / "only angle"),
# which the REST CLI approves unchanged.
_APPROVED_PLAN = ResearchPlan(
    subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
    depth=Depth.NORMAL,
)


async def _in_process_reference() -> tuple[ClaimLedger, str]:
    """The PARITY baseline: drive the offline workflow in-process to its terminal
    ledger + draft, approving CP1 with ``_APPROVED_PLAN`` and CP2 with empty
    (approve unchanged). This is exactly ``test_workflow``'s in-process path."""
    workflow = _normal_workflow()
    ledger = await _drive_through_checkpoints(
        workflow, approved_plan=_APPROVED_PLAN, cp3_reply="d", cp2_reply=""
    )
    draft = _drive_through_checkpoints.last_session_state["v2_draft"]
    return ledger, draft


def _make_remote(server_app) -> tuple[RemoteA2aAgent, httpx.AsyncClient]:
    """An in-process ``RemoteA2aAgent`` client over ``server_app`` (no real port)."""
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://localhost:8001",
    )
    remote = RemoteA2aAgent(
        name="localgpt_research_remote",
        agent_card="http://localhost:8001/a2a/localgpt_research/.well-known/agent-card.json",
        httpx_client=httpx_client,
        use_legacy=False,
    )
    return remote, httpx_client


async def _new_client_runner(remote: RemoteA2aAgent) -> tuple[Runner, str]:
    runner = Runner(
        app=App(
            name="a2a_client",
            root_agent=remote,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=InMemorySessionService(),
    )
    session = await runner.session_service.create_session(
        app_name="a2a_client", user_id="client-user"
    )
    return runner, session.id


async def _read_terminal_from_server(db_service, a2a_context_id) -> tuple[ClaimLedger, str]:
    """Read the terminal ledger (``event.output``) + approved draft (``v2_draft``)
    off the persisted A2A server session — where the result actually lands, since
    ADK's A2A surface does not project ``event.output`` to the client."""
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
    assert terminal_output is not None, "A2A resume did not produce a terminal output"
    return ClaimLedger.model_validate(terminal_output), server_session.state["v2_draft"]


# ── (A) + (C): A2A round-trip (CP1 + CP2) green; ledger/draft parity ────────────
async def test_a2a_roundtrip_full_hitl_matches_in_process(tmp_path):
    """A2A round-trip with full HITL (CP1→CP2), node-skip on resume, and ledger +
    byte-exact draft PARITY with the in-process workflow result."""
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    server_app, db_service = _build_a2a_stub_server(
        tmp_path, clarifier_calls=clarifier_calls, planner_calls=planner_calls
    )
    remote, httpx_client = _make_remote(server_app)
    client_runner, client_session_id = await _new_client_runner(remote)

    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    a2a_context_id = None
    cp1_iid = None
    cp2_iid = None
    for _ in range(8):
        pending = None  # (interrupt_id, reply_payload)
        async for event in client_runner.run_async(
            user_id="client-user", session_id=client_session_id, new_message=message
        ):
            meta = event.custom_metadata or {}
            if meta.get("a2a:context_id"):
                a2a_context_id = meta["a2a:context_id"]
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                if cp1_iid is None:
                    cp1_iid = iid
                    pending = (iid, _APPROVED_PLAN.model_dump(mode="json"))
                else:  # CP2 draft-approval — empty reply approves unchanged.
                    cp2_iid = iid
                    pending = (iid, {"result": ""})
        if pending is None:
            break
        message = types.Content(
            role="user", parts=[create_request_input_response(pending[0], pending[1])]
        )

    # ── (A) full HITL happened over the A2A protocol ─────────────────────────
    assert cp1_iid is not None, "CP1 never surfaced as an A2A input-required task"
    assert cp2_iid is not None, "CP2 never surfaced after the A2A resume"
    assert a2a_context_id is not None
    # Node-skip on resume: completed clarifier/planner bodies did NOT re-run.
    assert clarifier_calls == [1], f"clarifier re-ran on A2A resume: {clarifier_calls}"
    assert planner_calls == [1], f"planner re-ran on A2A resume: {planner_calls}"

    a2a_ledger, a2a_draft = await _read_terminal_from_server(db_service, a2a_context_id)
    await httpx_client.aclose()

    # ── (C) PARITY with the in-process result ────────────────────────────────
    ref_ledger, ref_draft = await _in_process_reference()
    assert {c.id for c in a2a_ledger.claims} == {c.id for c in ref_ledger.claims} == {"c1"}
    assert [c.status for c in a2a_ledger.claims] == [c.status for c in ref_ledger.claims]
    assert a2a_ledger.claims[0].status == ClaimStatus.KEPT
    assert a2a_ledger.kept_count("s1") == ref_ledger.kept_count("s1") == 1
    # The markdown draft is deterministic → byte-exact parity over the wire.
    assert a2a_draft == ref_draft


# ── (A): full checkpoint sequence (CP1 + CP3 + CP2) round-trips over A2A ─────────
async def test_a2a_roundtrip_deep_covers_cp3(tmp_path):
    """A DEEP plan surfaces the deep-only CP3 between CP1 and CP2, proving the FULL
    checkpoint sequence round-trips over the A2A protocol (and still reaches a
    terminal ledger with the upstream nodes checkpoint-skipped)."""
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    server_app, db_service = _build_a2a_stub_server(
        tmp_path, clarifier_calls=clarifier_calls, planner_calls=planner_calls
    )
    remote, httpx_client = _make_remote(server_app)
    client_runner, client_session_id = await _new_client_runner(remote)

    deep_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.DEEP,
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    a2a_context_id = None
    kinds_seen: set[str] = set()
    cp3_iids: list[str] = []
    cp1_seen = False
    for _ in range(8):
        pending = None
        async for event in client_runner.run_async(
            user_id="client-user", session_id=client_session_id, new_message=message
        ):
            meta = event.custom_metadata or {}
            if meta.get("a2a:context_id"):
                a2a_context_id = meta["a2a:context_id"]
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                if iid.startswith("cp3_"):
                    kinds_seen.add("cp3")
                    cp3_iids.append(iid)
                    pending = (iid, {"result": "d"})  # done, no URL edits
                elif not cp1_seen:
                    cp1_seen = True
                    kinds_seen.add("cp1")
                    pending = (iid, deep_plan.model_dump(mode="json"))
                else:
                    kinds_seen.add("cp2")
                    pending = (iid, {"result": ""})
        if pending is None:
            break
        message = types.Content(
            role="user", parts=[create_request_input_response(pending[0], pending[1])]
        )

    # The FULL checkpoint sequence surfaced over A2A, in deep order CP1→CP3→CP2.
    assert kinds_seen == {"cp1", "cp3", "cp2"}, f"full CP sequence not seen over A2A: {kinds_seen}"
    # One DEEP subtopic, target met after one pass → exactly one CP3 pause.
    assert cp3_iids == ["cp3_s1_0"], f"unexpected CP3 pauses over A2A: {cp3_iids}"
    assert clarifier_calls == [1], f"clarifier re-ran on A2A resume: {clarifier_calls}"
    assert planner_calls == [1], f"planner re-ran on A2A resume: {planner_calls}"

    # The resumed run reached a terminal ClaimLedger on the persisted server session.
    ledger, _draft = await _read_terminal_from_server(db_service, a2a_context_id)
    assert {c.id for c in ledger.claims} == {"c1"}
    assert ledger.kept_count("s1") == 1
    await httpx_client.aclose()


# ── (B) + (C): REST resume-by-invocation_id green; ledger/draft parity ──────────
async def test_rest_resume_by_invocation_id_matches_in_process(tmp_path, monkeypatch):
    """The REST ``/run_sse`` resume-by-``invocation_id`` variant drives a full run
    to a terminal ledger + draft, with ledger + byte-exact draft PARITY against
    the in-process result."""
    transport = _build_rest_stub_server(tmp_path, _normal_workflow())
    # Terminal stdin: approve CP1 ("a") then approve CP2 ("a"); both keep the
    # planner's plan / Writer draft unchanged — matching the in-process baseline.
    replies = iter(["a", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies))

    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        final_output, state = await run_cli("rust async runtimes", client=client)

    assert final_output is not None, "CLI never received a terminal output over REST"
    rest_ledger = ClaimLedger.model_validate(final_output)
    rest_draft = state["v2_draft"]

    ref_ledger, ref_draft = await _in_process_reference()
    assert {c.id for c in rest_ledger.claims} == {c.id for c in ref_ledger.claims} == {"c1"}
    assert rest_ledger.claims[0].status == ClaimStatus.KEPT
    assert rest_ledger.kept_count("s1") == ref_ledger.kept_count("s1") == 1
    assert rest_draft == ref_draft
