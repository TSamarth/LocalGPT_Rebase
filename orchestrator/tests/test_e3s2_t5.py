"""
T5 gate (E3.S2): Authoritative resume validation (byte-comparable) — architecture hard-gate #3.

Two test suites:

1. **Cross-restart resume gate** — extends the T1 gate to include the
   ``SessionExporterPlugin`` and assert byte-comparable artifacts.
   Phase 1: drive an offline workflow to the CP1 pause using a DB-backed runner
   with ``SessionExporterPlugin`` registered.
   Restart: build new DB service + new exporter over the same SQLite/sessions_dir.
   Phase 2: resume through CP2 to terminal; assert exporter artifacts exist and
   roundtrip correctly; assert NO stage.json.

2. **Byte-comparability vs v1 SessionStore (architecture hard-gate #3)** — proves
   exporter output is byte-identical to ``SessionStore`` output for the same data.
   This gate must pass before E5 can delete ``SessionStore``.

All tests are fully offline (canned ``@node`` stubs; no Ollama, no network).
"""
from __future__ import annotations

import warnings

from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
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
from app.session import SessionStore
from app.session_exporter import SessionExporterPlugin
from app.workflow import build_research_workflow

# ResumabilityConfig triggers an EXPERIMENTAL warning that is expected and noisy.
warnings.filterwarnings(
    "ignore",
    message=r".*ResumabilityConfig.*",
    category=UserWarning,
)

RAW_QUERY = "tell me about rust async runtimes"

# ── Offline stubs (copied from test_e3s2_t1.py; self-contained) ──────────────
# Factory functions so each test gets a fresh node instance.


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
    """Returns 1 KEPT claim (2 independent sources) on first call → stop-rule fires."""

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


def _build_test_app(workflow, *, name: str = "t5gate") -> App:
    return App(
        name=name,
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# Approved plan fed to CP1 (SHALLOW, 1 subtopic, target_evidence=1).
_APPROVE_PLAN = ResearchPlan(
    subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=1)],
    depth=Depth.SHALLOW,
)


# ── Suite A: Cross-restart resume gate ────────────────────────────────────────


async def test_authoritative_resume_with_exporter(tmp_path):
    """Gate: DB-backed runner + SessionExporterPlugin survive restart; artifacts byte-comparable.

    Phase 1  — drive offline workflow to CP1 pause using a DB-backed runner
               with ``SessionExporterPlugin`` registered.  Session and events
               written to a temp SQLite file.

    Restart  — build db_svc2 + exporter2 (new Python objects) over the SAME
               SQLite file and ``sessions_dir``, simulating a process kill +
               restart.  Assert session survived in DB with events.

    Phase 2  — resume via runner2: approve CP1, drive through CP2 to terminal.
               Assert terminal ClaimLedger ≥ 1 claim.

    Artifacts — after the run:
      - plan.json, claim_ledger.json, draft.md exist and are non-empty
      - plan.json roundtrips byte-comparably through ResearchPlan
      - claim_ledger.json parses to ≥ 1 claim
      - draft.md contains the writer stub body ("## Findings")
      - stage.json is NOT present
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path}/sessions.db"
    sessions_dir = tmp_path / "exporter_sessions"
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    workflow = _build_offline_workflow()
    app = _build_test_app(workflow, name="t5gate-a")

    # ── Phase 1: run to CP1 pause ─────────────────────────────────────────────
    db_svc1 = DatabaseSessionService(db_url=db_url)
    exporter1 = SessionExporterPlugin(sessions_dir=str(sessions_dir))
    runner1 = build_runner(app, session_service=db_svc1, plugins=[exporter1])
    session = await runner1.session_service.create_session(
        app_name=app.name, user_id="test-user"
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
    exporter2 = SessionExporterPlugin(sessions_dir=str(sessions_dir))
    runner2 = build_runner(app, session_service=db_svc2, plugins=[exporter2])

    # Assert session and events survived the restart.
    session2 = await db_svc2.get_session(
        app_name=app.name, user_id="test-user", session_id=session.id
    )
    assert session2 is not None, "session not found in DB after restart"
    assert len(session2.events) > 0, "session has no events in DB after restart"

    # ── Phase 2: resume via runner2 through CP2 → terminal ledger ────────────
    reply_part = create_request_input_response(
        interrupt_id, _APPROVE_PLAN.model_dump(mode="json")
    )
    new_message = types.Content(role="user", parts=[reply_part])

    final_output = None
    for _ in range(50):
        pending = None
        async for event in runner2.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                # Only CP2 on the SHALLOW path (no CP3); approve with empty reply.
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
    assert len(ledger.claims) >= 1, "terminal ledger has no claims"

    # ── Assert exporter artifacts exist and are non-empty ────────────────────
    session_dir = sessions_dir / session.id
    plan_path = session_dir / "plan.json"
    ledger_path = session_dir / "claim_ledger.json"
    draft_path = session_dir / "draft.md"

    assert plan_path.exists(), "plan.json not written by exporter"
    assert ledger_path.exists(), "claim_ledger.json not written by exporter"
    assert draft_path.exists(), "draft.md not written by exporter"

    plan_text = plan_path.read_text(encoding="utf-8")
    ledger_text = ledger_path.read_text(encoding="utf-8")
    draft_text = draft_path.read_text(encoding="utf-8")

    assert plan_text, "plan.json is empty"
    assert ledger_text, "claim_ledger.json is empty"
    assert draft_text, "draft.md is empty"

    # ── Assert byte-comparable content ────────────────────────────────────────
    # plan.json: roundtrip through ResearchPlan must be idempotent.
    parsed_plan = ResearchPlan.model_validate_json(plan_text)
    assert parsed_plan.model_dump_json(indent=2) == plan_text, (
        "plan.json fails byte-comparable roundtrip through ResearchPlan"
    )

    # claim_ledger.json: roundtrip through ClaimLedger must be idempotent.
    parsed_ledger = ClaimLedger.model_validate_json(ledger_text)
    assert len(parsed_ledger.claims) >= 1, "claim_ledger.json has no claims"
    assert parsed_ledger.model_dump_json(indent=2) == ledger_text, (
        "claim_ledger.json fails byte-comparable roundtrip through ClaimLedger"
    )

    # draft.md: must contain the writer stub body.
    assert "## Findings" in draft_text, "draft.md missing '## Findings'"

    # ── Assert NO stage.json in session dir ───────────────────────────────────
    assert not (session_dir / "stage.json").exists(), "exporter must NOT write stage.json"


# ── Suite B: Byte-comparability vs v1 SessionStore (architecture hard-gate #3) ─


async def test_exporter_byte_comparable_to_session_store(tmp_path):
    """Architecture hard-gate #3: exporter output is byte-identical to SessionStore output.

    Proves that ``SessionExporterPlugin`` writes exactly the same bytes that
    ``SessionStore.save_plan`` / ``save_ledger`` / ``save_draft`` would write
    for the same model data.  This gate must pass before E5 can delete
    ``SessionStore``.

    Approach:
      1. Run the offline workflow with InMemorySessionService + exporter to terminal.
      2. Read exporter's plan.json, claim_ledger.json, draft.md.
      3. Parse each file back into its Pydantic model; re-write via ``SessionStore``.
      4. Compare what the store wrote vs what the exporter wrote — byte-for-byte.

    ``config.SESSIONS_DIR`` is already monkeypatched by conftest's ``_isolated_dirs``
    autouse fixture to ``tmp_path / "sessions"``, so ``SessionStore`` writes there
    without touching the real ``data/`` directory.
    """
    exporter_sessions = tmp_path / "exporter_sessions"
    exporter = SessionExporterPlugin(sessions_dir=str(exporter_sessions))

    workflow = _build_offline_workflow()
    app = _build_test_app(workflow, name="t5gate-b")
    session_svc = InMemorySessionService()
    runner = build_runner(app, session_service=session_svc, plugins=[exporter])
    session = await session_svc.create_session(app_name=app.name, user_id="test-user")

    # Drive through CP1 and CP2 to terminal (same loop pattern as T2).
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    cp1_seen = False
    invocation_id = None
    new_message = message

    for _ in range(100):
        pending = None
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                invocation_id = event.invocation_id
                if not cp1_seen:
                    cp1_seen = True
                    pending = (iid, _APPROVE_PLAN.model_dump(mode="json"))
                else:
                    pending = (iid, {"result": ""})

        if pending is None:
            break
        reply_part = create_request_input_response(pending[0], pending[1])
        new_message = types.Content(role="user", parts=[reply_part])

    # Read exporter output.
    exporter_session_dir = exporter_sessions / session.id
    assert (exporter_session_dir / "plan.json").exists(), "exporter did not write plan.json"
    assert (exporter_session_dir / "claim_ledger.json").exists(), "exporter did not write claim_ledger.json"
    assert (exporter_session_dir / "draft.md").exists(), "exporter did not write draft.md"
    plan_text = (exporter_session_dir / "plan.json").read_text(encoding="utf-8")
    ledger_text = (exporter_session_dir / "claim_ledger.json").read_text(encoding="utf-8")
    draft_text = (exporter_session_dir / "draft.md").read_text(encoding="utf-8")

    # Parse into models and re-write via SessionStore.
    plan_model = ResearchPlan.model_validate_json(plan_text)
    ledger_model = ClaimLedger.model_validate_json(ledger_text)

    store = SessionStore(session_id="cmp-session")
    store.save_plan(plan_model)
    store.save_ledger(ledger_model)
    store.save_draft(draft_text)

    # Read what the store wrote.
    store_plan_text = (store.dir / SessionStore.PLAN_FILE).read_text(encoding="utf-8")
    store_ledger_text = (store.dir / SessionStore.LEDGER_FILE).read_text(encoding="utf-8")
    store_draft_text = (store.dir / SessionStore.DRAFT_FILE).read_text(encoding="utf-8")

    # Byte-for-byte comparison: exporter output must equal SessionStore output.
    assert plan_text == store_plan_text, (
        "plan.json: exporter output is NOT byte-identical to SessionStore.save_plan output"
    )
    assert ledger_text == store_ledger_text, (
        "claim_ledger.json: exporter output is NOT byte-identical to SessionStore.save_ledger output"
    )
    assert draft_text == store_draft_text, (
        "draft.md: exporter output is NOT byte-identical to SessionStore.save_draft output"
    )
