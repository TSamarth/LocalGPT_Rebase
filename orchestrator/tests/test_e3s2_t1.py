"""
T1 gate (E3.S2): DB-backed runner survives restart; session+events visible to new runner.

Two suites:

1. **Smoke tests** — ``build_runner`` factory wiring:
   - Returns a ``Runner`` whose ``session_service`` is a ``DatabaseSessionService``
     when a DB service is injected explicitly.
   - Defaults to ``DatabaseSessionService`` when no service is given (config path).

2. **Gate test** — cross-restart session survival:
   - Phase 1: drive an offline workflow to the CP1 ``RequestInput`` pause using a
     DB-backed runner.  The pause state is written to a temp SQLite file.
   - Simulate restart: build a SECOND ``DatabaseSessionService`` + runner over the
     SAME file (different Python objects, simulating a fresh process).
   - Assert the session is visible with at least one event.
   - Phase 2: resume via the second runner, answer CP2 with an empty reply (approve),
     and assert the run reaches a terminal ``ClaimLedger``.

All tests are fully offline (canned ``@node`` stubs; no Ollama, no network).
"""
from __future__ import annotations

import warnings

import pytest
from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import DatabaseSessionService
from google.adk.workflow import node
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types

from app.runner import build_runner
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    ClarifyResult,
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceRef,
    Subtopic,
)
from app.workflow import build_research_workflow

# ResumabilityConfig triggers an EXPERIMENTAL warning that is expected and noisy.
warnings.filterwarnings(
    "ignore",
    message=r".*ResumabilityConfig.*",
    category=UserWarning,
)

RAW_QUERY = "tell me about rust async runtimes"

# ── Offline stubs ─────────────────────────────────────────────────────────────
# Factory functions (not module-level decorated callables) so each test gets
# a fresh node instance — the same pattern used in test_workflow.py.


def _clarifier_stub():
    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(status="clear", normalized_query="normalized: " + str(node_input).strip())

    return _stub


def _planner_stub():
    """Shallow plan with ONE subtopic and target_evidence=1 (no CP3; fast loop exit)."""

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=1)],
            depth=Depth.SHALLOW,
        )

    return _stub


def _acquirer_stub():
    @node
    async def _stub(node_input: str) -> list[ScoredURL]:
        return [
            ScoredURL(
                url="https://a.example/doc",
                score=0.9,
                strategy=CrawlStrategy.CRAWL,
                etld1="a.example",
            )
        ]

    return _stub


def _extractor_stub():
    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]

    return _stub


def _verifier_stub():
    """Returns 1 KEPT claim on the first call → stop-rule target_met fires."""

    calls = {"i": 0}

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        i = calls["i"]
        calls["i"] += 1
        if i == 0:
            return ClaimLedger(
                claims=[
                    Claim(
                        id="c1",
                        subtopic_id="s1",
                        text="a claim",
                        status=ClaimStatus.UNCORROBORATED,
                        sources=[
                            SourceRef(url="https://a.example/1", etld1="a.example", quote="q1"),
                            SourceRef(url="https://b.example/1", etld1="b.example", quote="q2"),
                        ],
                    )
                ]
            )
        return ClaimLedger(claims=[])

    return _stub


def _writer_stub():
    @node
    async def _stub(node_input: str) -> str:
        return "## Findings\n\nWriter body."

    return _stub


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_offline_workflow():
    """Fully-offline workflow: all nodes are canned stubs."""
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_verifier_stub(),
        writer_node=_writer_stub(),
    )


def _build_test_app(workflow) -> App:
    return App(
        name="t1gate",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# Approved plan fed to CP1 (SHALLOW, 1 subtopic).
_APPROVE_PLAN = ResearchPlan(
    subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=1)],
    depth=Depth.SHALLOW,
)

# ── Smoke tests ───────────────────────────────────────────────────────────────


def test_build_runner_with_explicit_db_service(tmp_path):
    """build_runner(app, session_service=<DB svc>) → runner.session_service is that service."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/smoke.db"
    svc = DatabaseSessionService(db_url=db_url)
    app = _build_test_app(_build_offline_workflow())
    runner = build_runner(app, session_service=svc)
    assert isinstance(runner.session_service, DatabaseSessionService)
    assert runner.session_service is svc


def test_build_runner_default_uses_database_service(tmp_path, monkeypatch):
    """build_runner(app) with no session_service defaults to DatabaseSessionService."""
    import app.config as config_module

    db_url = f"sqlite+aiosqlite:///{tmp_path}/default.db"
    monkeypatch.setattr(config_module.config, "SESSION_DB_URL", db_url)

    test_app = _build_test_app(_build_offline_workflow())
    runner = build_runner(test_app)
    assert isinstance(runner.session_service, DatabaseSessionService)


# ── Gate test ─────────────────────────────────────────────────────────────────


async def test_db_runner_survives_restart(tmp_path):
    """Gate: DB-backed runner survives restart; session+events visible to new runner.

    Phase 1  — drive an offline workflow to the CP1 RequestInput pause using a
               DB-backed runner (runner1 / db_svc1).  The session and events are
               written to a temp SQLite file at *tmp_path*.

    Restart  — build db_svc2 (fresh object) and runner2 over the SAME SQLite file,
               simulating a process kill + restart.

    Assert   — db_svc2.get_session returns the session with at least one event.

    Phase 2  — resume via runner2: answer CP1 with the approve plan, answer CP2
               with an empty reply (approve the draft unchanged), and assert the
               run reaches a terminal ClaimLedger.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path}/sessions.db"
    workflow = _build_offline_workflow()
    test_app = _build_test_app(workflow)
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    # ── Phase 1: run to CP1 pause ─────────────────────────────────────────────
    db_svc1 = DatabaseSessionService(db_url=db_url)
    runner1 = build_runner(test_app, session_service=db_svc1)
    session = await runner1.session_service.create_session(
        app_name=test_app.name, user_id="test-user"
    )

    interrupt_id = None
    invocation_id = None
    async for event in runner1.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            interrupt_id = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id

    assert interrupt_id is not None, "workflow did not pause at CP1"
    assert invocation_id is not None, "invocation_id not captured"

    # ── Simulate restart: new objects over the SAME SQLite file ──────────────
    db_svc2 = DatabaseSessionService(db_url=db_url)
    runner2 = build_runner(test_app, session_service=db_svc2)

    # Assert session and events survived the restart.
    session2 = await db_svc2.get_session(
        app_name=test_app.name, user_id="test-user", session_id=session.id
    )
    assert session2 is not None, "session not found in DB after restart"
    assert len(session2.events) > 0, "session has no events in DB after restart"

    # ── Phase 2: resume via runner2 through CP2 → terminal ledger ────────────
    reply_part = create_request_input_response(
        interrupt_id, _APPROVE_PLAN.model_dump(mode="json")
    )
    new_message = types.Content(role="user", parts=[reply_part])

    final_output = None
    for _ in range(50):  # generous cap; real runs need far fewer hops
        pending = None
        async for event in runner2.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                # Only CP2 appears on the SHALLOW path (no CP3); approve with "".
                pending = (iid, {"result": ""})
            if event.output is not None:
                final_output = event.output

        if pending is None:
            break
        reply_part = create_request_input_response(pending[0], pending[1])
        new_message = types.Content(role="user", parts=[reply_part])

    assert final_output is not None, "runner2 never produced a terminal output"
    ledger = ClaimLedger.model_validate(final_output)
    assert isinstance(ledger, ClaimLedger)
    # The verifier stub returns 1 KEPT claim for s1 → stop on target_met.
    assert len(ledger.claims) >= 1
