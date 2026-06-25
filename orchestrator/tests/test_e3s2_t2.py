"""
T2 gate (E3.S2): SessionExporterPlugin — ADK session state → disk.

Four test suites:

1. **Incremental writes** — checkpoint-by-checkpoint file-existence probe:
   Phase 1 (to CP1 pause): plan.json absent.
   Phase 2 (CP1 approve → CP2 pause): plan.json + claim_ledger.json present,
   draft.md absent.
   Phase 3 (CP2 approve → terminal): draft.md present.

2. **Byte-comparability gate** — run with BOTH the exporter plugin AND the
   SessionStore write-through active simultaneously. After terminal output:
     exporter plan.json   == store.load_plan().model_dump_json(indent=2)
     exporter ledger.json == store.load_ledger().model_dump_json(indent=2)
     exporter draft.md    == store.load_draft()
   Proves byte-identical output before the T3 seam removal.

3. **No stage.json** — exporter never writes stage.json.

4. **Idempotent after_run** — ``after_run_callback`` called twice on the
   same session state leaves file contents unchanged.

All tests are fully offline (canned ``@node`` stubs; no Ollama, no network).
Uses ``InMemorySessionService`` for runner isolation.
"""
from __future__ import annotations

import warnings

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.session import Session
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

# ── Shared approve plan (SHALLOW, target_evidence=2 matches TARGET_EVIDENCE_SHALLOW) ──
# Using target_evidence=2 means parse_plan's apply_depth_targets leaves it unchanged
# for SHALLOW depth → store.save_plan(plan) and exporter produce identical JSON.
_APPROVE_PLAN = ResearchPlan(
    subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=2)],
    depth=Depth.SHALLOW,
)


# ── Offline stubs ─────────────────────────────────────────────────────────────


def _clarifier_stub():
    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(
            status="clear",
            normalized_query="normalized: " + str(node_input).strip(),
        )

    return _stub


def _planner_stub():
    """SHALLOW plan: 1 subtopic, target_evidence=2 (matches TARGET_EVIDENCE_SHALLOW).

    apply_depth_targets leaves target_evidence=2 unchanged for SHALLOW → parse_plan
    is idempotent on this plan, so store.save_plan and exporter produce the same JSON.
    """

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=2)],
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


def _verifier_stub_one_kept():
    """Returns 1 KEPT claim (2 independent sources) on first call; empty thereafter.

    With target_evidence=2 and MIN_NEW_CLAIMS=2:
    - Pass 0: 1 new claim, new_claims=1. After pass: iteration=1, new_claims=1.
    - Stop check: kept_count(1) >= target_evidence(2)? No.
      iteration(1) >= budget(SHALLOW=2)? No.
      iteration(1)>=1 and new_claims(1) < MIN_NEW_CLAIMS(2)? 1 < 2 → Yes → STOP.
    Loop exits after exactly one pass via diminishing returns.
    """
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
                            SourceRef(
                                url="https://a.example/1",
                                etld1="a.example",
                                quote="q1",
                            ),
                            SourceRef(
                                url="https://b.example/1",
                                etld1="b.example",
                                quote="q2",
                            ),
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


# ── App + runner builders ──────────────────────────────────────────────────────


def _build_offline_workflow(*, store=None):
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_verifier_stub_one_kept(),
        writer_node=_writer_stub(),
        store=store,
    )


def _build_app(workflow) -> App:
    return App(
        name="t2gate",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# ── Low-level driver: drives ONE complete invocation through CP1 + CP2 ─────────
async def _run_complete(
    runner,
    session,
    *,
    approved_plan: ResearchPlan,
    cp2_reply: str = "",
) -> ClaimLedger:
    """Drive ``runner`` through CP1 (approve) and CP2 (approve/edit) to terminal.

    SHALLOW plan → no CP3 pauses.  ``cp2_reply`` defaults to ``""`` (approve,
    draft unchanged).  Returns the terminal ``ClaimLedger``.
    """
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    cp1_seen = False
    invocation_id = None
    new_message = message
    final_output = None

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
                    pending = (iid, approved_plan.model_dump(mode="json"))
                else:
                    pending = (iid, {"result": cp2_reply})
            if event.output is not None:
                final_output = event.output

        if pending is None:
            break
        reply_part = create_request_input_response(pending[0], pending[1])
        new_message = types.Content(role="user", parts=[reply_part])

    assert final_output is not None, "workflow never produced a terminal output"
    return ClaimLedger.model_validate(final_output)


# ── Test 1: incremental writes (checkpoint-by-checkpoint) ──────────────────────


async def test_exporter_incremental_writes(tmp_path):
    """Files appear on disk at the right checkpoint — plan before CP2, draft after CP2.

    Phase 1 → CP1 pause  : plan.json absent (v2_plan event not yet fired)
    Phase 2 → CP2 pause  : plan.json + claim_ledger.json present,
                           draft.md absent (v2_draft fires after CP2 approval)
    Phase 3 → terminal   : draft.md present
    """
    sessions_dir = tmp_path / "sessions"
    exporter = SessionExporterPlugin(sessions_dir=str(sessions_dir))

    workflow = _build_offline_workflow()
    app = _build_app(workflow)
    session_svc = InMemorySessionService()
    runner = build_runner(app, session_service=session_svc, plugins=[exporter])
    session = await session_svc.create_session(app_name="t2gate", user_id="test-user")
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    # ── Phase 1: run to CP1 pause ──────────────────────────────────────────
    cp1_interrupt_id = None
    invocation_id = None
    async for event in runner.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            cp1_interrupt_id = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id

    assert cp1_interrupt_id is not None, "workflow did not pause at CP1"

    # After CP1 pause: no v2_* events have fired yet → plan.json absent
    session_dir = sessions_dir / session.id
    assert not (session_dir / "plan.json").exists(), (
        "plan.json must not exist before CP1 approval"
    )
    assert not (session_dir / "claim_ledger.json").exists()
    assert not (session_dir / "draft.md").exists()

    # ── Phase 2: answer CP1 → run to CP2 pause ──────────────────────────────
    reply_part = create_request_input_response(
        cp1_interrupt_id, _APPROVE_PLAN.model_dump(mode="json")
    )
    new_message = types.Content(role="user", parts=[reply_part])

    cp2_interrupt_id = None
    async for event in runner.run_async(
        user_id="test-user",
        session_id=session.id,
        invocation_id=invocation_id,
        new_message=new_message,
    ):
        if has_request_input_function_call(event):
            iid = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id
            if not iid.startswith("cp3_"):
                cp2_interrupt_id = iid

    assert cp2_interrupt_id is not None, "workflow did not pause at CP2"

    # After CP2 pause: v2_plan + v2_ledger events fired; v2_draft not yet
    assert (session_dir / "plan.json").exists(), (
        "plan.json must exist after CP1 approval"
    )
    assert (session_dir / "claim_ledger.json").exists(), (
        "claim_ledger.json must exist after loop completes"
    )
    assert not (session_dir / "draft.md").exists(), (
        "draft.md must not exist before CP2 approval"
    )

    # ── Phase 3: answer CP2 → run to terminal ──────────────────────────────
    reply_part = create_request_input_response(cp2_interrupt_id, {"result": ""})
    new_message = types.Content(role="user", parts=[reply_part])

    async for _event in runner.run_async(
        user_id="test-user",
        session_id=session.id,
        invocation_id=invocation_id,
        new_message=new_message,
    ):
        pass

    # After terminal: all three artifacts present
    assert (session_dir / "draft.md").exists(), (
        "draft.md must exist after CP2 approval"
    )
    assert (session_dir / "plan.json").exists()
    assert (session_dir / "claim_ledger.json").exists()


# ── Test 2: byte-comparability gate ────────────────────────────────────────────


async def test_exporter_byte_comparability(tmp_path):
    """Exporter files are byte-identical to SessionStore files on canned inputs.

    Run with BOTH exporter plugin (via ``build_runner``) AND ``store=`` write-
    through active simultaneously.  After terminal output compare:
      exporter plan.json   == store.load_plan().model_dump_json(indent=2)
      exporter ledger.json == store.load_ledger().model_dump_json(indent=2)
      exporter draft.md    == store.load_draft()

    Byte-comparability holds because:
    - plan: ``store.save_plan(plan)`` and exporter both call parse_plan on the
      same plan content (SHALLOW, target_evidence=2 unchanged by apply_depth_targets)
      → identical JSON.
    - ledger: ``store.save_ledger(ledger)`` at end of loop and exporter's last
      v2_ledger event carry the same accumulated ledger.
    - draft: ``store.save_draft(approved_draft)`` and v2_draft event carry the
      same ``approved_draft`` string.
    """
    exporter_sessions = tmp_path / "exporter_sessions"
    exporter = SessionExporterPlugin(sessions_dir=str(exporter_sessions))

    store = SessionStore.create(RAW_QUERY)
    workflow = _build_offline_workflow(store=store)  # both active
    app = _build_app(workflow)
    session_svc = InMemorySessionService()
    runner = build_runner(app, session_service=session_svc, plugins=[exporter])
    session = await session_svc.create_session(app_name="t2gate", user_id="test-user")

    # Drive through all checkpoints to terminal
    await _run_complete(runner, session, approved_plan=_APPROVE_PLAN)

    session_dir = exporter_sessions / session.id

    # plan.json: byte-identical to store.load_plan().model_dump_json(indent=2)
    store_plan = store.load_plan()
    assert store_plan is not None, "store must have plan.json after run"
    exporter_plan_text = (session_dir / "plan.json").read_text(encoding="utf-8")
    assert exporter_plan_text == store_plan.model_dump_json(indent=2), (
        "plan.json content mismatch between exporter and SessionStore"
    )

    # claim_ledger.json: byte-identical to store.load_ledger().model_dump_json(indent=2)
    store_ledger = store.load_ledger()
    exporter_ledger_text = (session_dir / "claim_ledger.json").read_text(encoding="utf-8")
    assert exporter_ledger_text == store_ledger.model_dump_json(indent=2), (
        "claim_ledger.json content mismatch between exporter and SessionStore"
    )

    # draft.md: byte-identical to store.load_draft()
    store_draft = store.load_draft()
    assert store_draft is not None, "store must have draft.md after run"
    exporter_draft = (session_dir / "draft.md").read_text(encoding="utf-8")
    assert exporter_draft == store_draft, (
        "draft.md content mismatch between exporter and SessionStore"
    )


# ── Test 3: no stage.json ──────────────────────────────────────────────────────


async def test_exporter_no_stage_json(tmp_path):
    """Exporter never writes stage.json (retired as v2 truth in E3.S2)."""
    sessions_dir = tmp_path / "sessions"
    exporter = SessionExporterPlugin(sessions_dir=str(sessions_dir))

    workflow = _build_offline_workflow()
    app = _build_app(workflow)
    session_svc = InMemorySessionService()
    runner = build_runner(app, session_service=session_svc, plugins=[exporter])
    session = await session_svc.create_session(app_name="t2gate", user_id="test-user")

    await _run_complete(runner, session, approved_plan=_APPROVE_PLAN)

    session_dir = sessions_dir / session.id
    assert not (session_dir / "stage.json").exists(), (
        "exporter must NOT write stage.json"
    )
    # Confirm the 3 expected files do exist (sanity check)
    assert (session_dir / "plan.json").exists()
    assert (session_dir / "claim_ledger.json").exists()
    assert (session_dir / "draft.md").exists()


# ── Test 4: idempotent after_run ───────────────────────────────────────────────


async def test_exporter_after_run_idempotent(tmp_path):
    """Calling after_run_callback twice with the same state leaves files unchanged.

    Constructs a minimal ``InvocationContext`` with a ``Session`` whose state
    contains all three v2_* keys.  Calls ``after_run_callback`` twice and asserts
    that file contents are identical after the second call.
    """
    sessions_dir = tmp_path / "sessions"
    exporter = SessionExporterPlugin(sessions_dir=str(sessions_dir))

    plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="idempotent angle", target_evidence=2)],
        depth=Depth.SHALLOW,
    )
    ledger = ClaimLedger(
        claims=[
            Claim(
                id="c1",
                subtopic_id="s1",
                text="idempotent claim",
                status=ClaimStatus.KEPT,
                sources=[
                    SourceRef(url="https://a.example/1", etld1="a.example", quote="q1"),
                    SourceRef(url="https://b.example/1", etld1="b.example", quote="q2"),
                ],
            )
        ]
    )
    draft_text = "## Idempotent Test\n\nDraft content for idempotency check."

    session = Session(
        id="idempotent-session-t2",
        app_name="t2gate",
        user_id="test-user",
        state={
            "v2_plan": plan.model_dump(mode="json"),
            "v2_ledger": ledger.model_dump(mode="json"),
            "v2_draft": draft_text,
        },
    )

    ctx = InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="test-inv-id-idempotent",
        session=session,
    )

    # First call
    await exporter.after_run_callback(invocation_context=ctx)

    session_dir = sessions_dir / session.id
    plan_text_1 = (session_dir / "plan.json").read_text(encoding="utf-8")
    ledger_text_1 = (session_dir / "claim_ledger.json").read_text(encoding="utf-8")
    draft_text_1 = (session_dir / "draft.md").read_text(encoding="utf-8")

    # Second call — must produce identical files
    await exporter.after_run_callback(invocation_context=ctx)

    assert (session_dir / "plan.json").read_text(encoding="utf-8") == plan_text_1, (
        "plan.json changed on second after_run_callback call"
    )
    assert (session_dir / "claim_ledger.json").read_text(encoding="utf-8") == ledger_text_1, (
        "claim_ledger.json changed on second after_run_callback call"
    )
    assert (session_dir / "draft.md").read_text(encoding="utf-8") == draft_text_1, (
        "draft.md changed on second after_run_callback call"
    )

    # Contents are what we expect (not just unchanged-from-first-call)
    assert plan_text_1 == plan.model_dump_json(indent=2)
    assert ledger_text_1 == ledger.model_dump_json(indent=2)
    assert draft_text_1 == draft_text
