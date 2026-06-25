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


def _acquirer_stub() -> object:
    """Canned acquirer node: one ScoredURL, no model/MCP call."""

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


def _extractor_stub() -> object:
    """Canned extractor node: returns page-ids the loop discards (as v1)."""

    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]

    return _stub


WRITER_BODY = "## Findings\n\nWriter prose body for the draft."


def _writer_stub() -> object:
    """Canned writer node: returns a fixed markdown body string, no model call.

    The default Writer is a live ``LlmAgent``; injecting this stub keeps the
    offline builders from reaching Ollama. With no ``output_schema`` the real
    Writer hands back a ``str`` body, so the stub returns a ``str`` too.
    """

    @node
    async def _stub(node_input: str) -> str:
        return WRITER_BODY

    return _stub


def _kept_claim(claim_id: str, subtopic_id: str) -> Claim:
    """A claim that enriches to KEPT — two independent (distinct-etld1) sources."""
    return Claim(
        id=claim_id,
        subtopic_id=subtopic_id,
        text=f"assertion {claim_id}",
        status=ClaimStatus.UNCORROBORATED,  # recomputed to KEPT by enrich_ledger
        sources=[
            SourceRef(url="https://a.example/1", etld1="a.example", quote="q1"),
            SourceRef(url="https://b.example/1", etld1="b.example", quote="q2"),
        ],
    )


def _uncorroborated_claim(claim_id: str, subtopic_id: str) -> Claim:
    """A claim that stays UNCORROBORATED — a single source (no corroboration)."""
    return Claim(
        id=claim_id,
        subtopic_id=subtopic_id,
        text=f"assertion {claim_id}",
        status=ClaimStatus.UNCORROBORATED,
        sources=[SourceRef(url="https://a.example/1", etld1="a.example", quote="q1")],
    )


def _verifier_stub_from_passes(pass_ledgers: list[ClaimLedger]) -> object:
    """Canned verifier node driven by a per-pass script.

    Each ``ctx.run_node(verifier, ...)`` call returns the next ledger in
    ``pass_ledgers``; once exhausted it returns an empty ledger (adds nothing).
    A list-index closure makes the controlled claim sets drive each stop-rule exit
    deterministically. The loop re-applies ``enrich_ledger`` to the returned
    ledger, so claim status is recomputed from source independence.
    """
    calls = {"i": 0}

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        i = calls["i"]
        calls["i"] += 1
        if i < len(pass_ledgers):
            return pass_ledgers[i]
        return ClaimLedger(claims=[])

    return _stub


def _build_loop_workflow(*, planner_node, verifier_node, store=None):
    """Build a fully-offline workflow with all five nodes injected (canned)."""
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=planner_node,
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier_node,
        writer_node=_writer_stub(),
        store=store,
    )


def _single_subtopic_planner_stub(target_evidence: int, depth: Depth) -> object:
    """Planner stub: ONE subtopic with an explicit target_evidence, so a single
    while-loop drives one stop-rule exit. ``parse_plan`` re-applies the depth
    policy, so the SHALLOW/NORMAL target is what config maps; we use the matching
    config target to keep the asserted exit condition unambiguous."""

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[Subtopic(id="s1", question="only angle", target_evidence=target_evidence)],
            depth=depth,
        )

    return _stub


def _counting_clarifier_stub(counter: list[int]) -> object:
    """Clarifier stub that records each execution by appending to ``counter``.

    The list-append is the instrumentation: it fires inside the node body, so it
    only grows when ADK actually *executes* this node. On resume, a completed
    (``rerun_on_resume=False``) node returns its cached output instead of running
    its body — so the counter must NOT grow. That is the T5 gate proof.
    """

    @node
    async def _stub(node_input: str) -> ClarifyResult:
        counter.append(1)
        return ClarifyResult(status="clear", normalized_query="normalized: " + str(node_input).strip())

    return _stub


def _counting_planner_stub(counter: list[int]) -> object:
    """Planner stub that records each execution by appending to ``counter`` (see
    :func:`_counting_clarifier_stub` for why a body-side counter is the proof)."""

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        counter.append(1)
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


async def _drive_to_cp1_and_resume(workflow, *, approved_plan: ResearchPlan) -> ClaimLedger:
    """Drive the slice to the CP1 pause, resume with ``approved_plan``, run the loop.

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

    The ``research`` node now runs the per-subtopic research loop after CP1, so
    the terminal output is the accumulated ``ClaimLedger`` (parsed from
    ``Event.output``, which ADK serializes via ``model_dump()`` — hence the
    ``model_validate``).
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
    return ClaimLedger.model_validate(final_output)


# ── Case (a): slice runs START→CP1→resume(approve)→loop and returns the ledger ──
async def test_slice_returns_accumulated_ledger():
    # The approved plan has 2 subtopics, each target_evidence=1. The verifier stub
    # returns ONE KEPT claim per subtopic, so each subtopic's stop-rule fires on
    # target-met after a single pass — the loop accumulates a 2-claim ledger.
    verifier = _verifier_stub_from_passes(
        [
            ClaimLedger(claims=[_kept_claim("c1", "s1")]),
            ClaimLedger(claims=[_kept_claim("c2", "s2")]),
        ]
    )
    workflow = _build_loop_workflow(planner_node=_planner_stub(), verifier_node=verifier)
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=1),
            Subtopic(id="s2", question="angle 2", target_evidence=1),
        ],
        depth=Depth.DEEP,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert isinstance(result, ClaimLedger)
    # One KEPT claim accumulated per subtopic across the loop.
    assert {c.id for c in result.claims} == {"c1", "c2"}
    assert result.kept_count("s1") == 1
    assert result.kept_count("s2") == 1


# ── Case (b) (T4 gate): an EDITED plan supplied at CP1 drives the loop ───────────
async def test_cp1_edited_plan_drives_loop():
    # The human edits the plan at CP1 down to ONE subtopic. The loop must run over
    # the EDITED plan (not the planner's 2-subtopic output): a single KEPT claim
    # for the surviving subtopic proves the resumed plan is what drives the loop.
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = _build_loop_workflow(planner_node=_planner_stub(), verifier_node=verifier)
    edited = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="EDITED: only rust async runtime fairness", target_evidence=1),
        ],
        depth=Depth.DEEP,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=edited)

    assert isinstance(result, ClaimLedger)
    assert {c.id for c in result.claims} == {"c1"}
    assert result.claims[0].subtopic_id == "s1"
    assert result.kept_count("s1") == 1


# ── T6: SessionStore write-through lands ledger.json during the run ──────────────
async def test_store_write_through_persists_ledger():
    store = SessionStore.create(RAW_QUERY)
    verifier = _verifier_stub_from_passes(
        [
            ClaimLedger(claims=[_kept_claim("c1", "s1")]),
            ClaimLedger(claims=[_kept_claim("c2", "s2")]),
        ]
    )
    workflow = _build_loop_workflow(
        planner_node=_planner_stub(), verifier_node=verifier, store=store
    )
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=1),
            Subtopic(id="s2", question="angle 2", target_evidence=1),
        ],
        depth=Depth.DEEP,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    ledger_path = store.dir / SessionStore.LEDGER_FILE
    assert ledger_path.exists(), "T6 write-through did not persist ledger.json"
    persisted = store.load_ledger()
    assert isinstance(persisted, ClaimLedger)
    # The persisted ledger is the loop's accumulated product.
    assert {c.id for c in persisted.claims} == {"c1", "c2"}


# ── T2 gate: each of the 3 stop-rule exits fires (target / diminishing / budget) ─
async def test_stop_rule_exit_target_met():
    """Exit 1 — target_evidence met: kept_count reaches the subtopic target."""
    # One subtopic, target_evidence=2. The first pass yields 2 KEPT claims, so
    # kept_count(s1)==2 >= target after a single pass → stop on target-met.
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1"), _kept_claim("c2", "s1")])]
    )
    workflow = _build_loop_workflow(
        planner_node=_single_subtopic_planner_stub(2, Depth.SHALLOW),
        verifier_node=verifier,
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=2)],
        depth=Depth.SHALLOW,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert result.kept_count("s1") == 2
    # Exactly one pass ran (target met immediately) → no extra accumulation.
    assert len(result.claims) == 2


async def test_stop_rule_exit_diminishing_returns():
    """Exit 2 — diminishing returns: a completed pass adds < MIN_NEW_CLAIMS."""
    # target_evidence is high so target-met never fires; budget (NORMAL=4) is not
    # hit first. Pass 1 adds 2 new UNCORROBORATED claims (>= MIN_NEW_CLAIMS=2, so
    # no diminishing yet); pass 2 RE-returns the SAME ids (dedup → 0 new) so
    # new_claims (0) < MIN_NEW_CLAIMS → stop on diminishing returns.
    assert config.MIN_NEW_CLAIMS == 2
    assert config.MAX_ITER_NORMAL >= 3
    pass1 = ClaimLedger(
        claims=[_uncorroborated_claim("c1", "s1"), _uncorroborated_claim("c2", "s1")]
    )
    verifier = _verifier_stub_from_passes([pass1, pass1])  # pass 2 duplicates pass 1
    workflow = _build_loop_workflow(
        planner_node=_single_subtopic_planner_stub(99, Depth.NORMAL),
        verifier_node=verifier,
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=99)],
        depth=Depth.NORMAL,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    # Two passes ran (pass 2 added 0 new → diminishing); ledger holds 2 deduped.
    assert result.kept_count("s1") == 0
    assert {c.id for c in result.claims} == {"c1", "c2"}


async def test_stop_rule_exit_budget_hit():
    """Exit 3 — budget hit: iteration reaches depth_budget without target/diminish."""
    # target never met (claims stay UNCORROBORATED) and every pass adds >= 2 NEW
    # claims (so diminishing never fires), so only the SHALLOW budget (2 passes)
    # can stop the loop → exit on budget hit at iteration == MAX_ITER_SHALLOW.
    budget = config.MAX_ITER_SHALLOW
    assert budget == 2
    passes = [
        ClaimLedger(
            claims=[
                _uncorroborated_claim(f"c{p}a", "s1"),
                _uncorroborated_claim(f"c{p}b", "s1"),
            ]
        )
        for p in range(budget + 2)  # more than enough; budget stops it first
    ]
    verifier = _verifier_stub_from_passes(passes)
    workflow = _build_loop_workflow(
        planner_node=_single_subtopic_planner_stub(99, Depth.SHALLOW),
        verifier_node=verifier,
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=99)],
        depth=Depth.SHALLOW,
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert result.kept_count("s1") == 0
    # Exactly ``budget`` passes ran, each adding 2 unique claims.
    assert len(result.claims) == 2 * budget


# ── Case (c) (T5 — HARD GATE): resume re-runs CP1 only; clarify/plan skipped ────
async def test_resume_reruns_cp1_only():
    """The de-risking gate before the E2.S2 loop port.

    This proves ADK's dynamic-workflow auto-checkpointing: when a run is killed at
    the CP1 ``RequestInput`` pause and later resumed by ``invocation_id``, the
    already-completed child nodes (clarifier, planner) are NOT re-executed — only
    the interrupted CP1 path resumes. If resume re-ran clarify/plan, every step
    would repeat on every checkpoint, which would make the loop port unsafe.

    Proof = body-side call counters. Each counting stub appends to its list when
    its body executes. We assert each counter is 1 after phase-1 (each ran once to
    reach CP1) and STILL 1 after resume (NOT 2 — they were checkpoint-skipped).

    Kill/resume modeling: we keep ONE ``InMemoryRunner`` (one ``session_service``,
    so the in-memory checkpoint state survives) across the kill boundary. The
    "kill" is abandoning the phase-1 generator at the pause and starting a fresh
    ``run_async`` resume call — a brand-new runner would lose all checkpoints
    (amnesia, not a kill). The assertion that matters is the counters, not runner
    identity: a re-execution would bump them regardless of runner instance.
    """
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    # Inject canned loop nodes too, so the resumed run drives the full loop fully
    # offline. The counting clarifier/planner are the gate instrumentation.
    verifier = _verifier_stub_from_passes(
        [
            ClaimLedger(claims=[_kept_claim("c1", "s1")]),
            ClaimLedger(claims=[_kept_claim("c2", "s2")]),
        ]
    )
    workflow = build_research_workflow(
        clarifier_node=_counting_clarifier_stub(clarifier_calls),
        planner_node=_counting_planner_stub(planner_calls),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )

    app = _build_app(workflow)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    # ── Phase 1: run to the CP1 pause (the "kill" point) ───────────────────────
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
    # Each child node ran exactly once to drive START → CP1.
    assert clarifier_calls == [1], f"phase-1 clarifier runs != 1: {clarifier_calls}"
    assert planner_calls == [1], f"phase-1 planner runs != 1: {planner_calls}"

    # ── "Kill": phase-1 generator is abandoned at the pause. We resume on the ──
    # SAME runner/session_service, so the checkpoint state is intact.
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=config.TARGET_EVIDENCE_DEEP),
            Subtopic(id="s2", question="angle 2", target_evidence=config.TARGET_EVIDENCE_DEEP),
        ],
        depth=Depth.DEEP,
    )
    reply_part = create_request_input_response(
        interrupt_id, approve_plan.model_dump(mode="json")
    )
    reply = types.Content(role="user", parts=[reply_part])

    # ── Phase 2: resume by invocation_id ───────────────────────────────────────
    final_output = None
    async for event in runner.run_async(
        user_id="test-user",
        session_id=session.id,
        invocation_id=invocation_id,
        new_message=reply,
    ):
        if event.output is not None:
            final_output = event.output

    # ── THE GATE: clarify/plan were auto-checkpoint-skipped on resume ──────────
    # Counters are STILL 1 (not 2): their bodies did NOT re-execute. Only the CP1
    # path resumed. If either were 2, the gate FAILS (resume re-ran a child).
    assert clarifier_calls == [1], f"GATE FAIL: clarifier re-ran on resume: {clarifier_calls}"
    assert planner_calls == [1], f"GATE FAIL: planner re-ran on resume: {planner_calls}"

    # ── And the resumed run reached a terminal ClaimLedger via the CP1 path + ──
    # the research loop (now running after CP1). One KEPT claim accumulated per
    # subtopic before each subtopic stopped on diminishing returns.
    assert final_output is not None, "resume did not produce a terminal output"
    result = ClaimLedger.model_validate(final_output)
    assert {c.id for c in result.claims} == {"c1", "c2"}
    assert result.kept_count("s1") == 1
    assert result.kept_count("s2") == 1


# ── T5: CP3 deep-only pass-through hook ──────────────────────────────────────────
def _recording_cp3_hook(calls: list[tuple[str, int]]):
    """A CP3 hook that records every (subtopic.id, iteration) it is called with."""

    def _hook(subtopic: Subtopic, iteration: int) -> None:
        calls.append((subtopic.id, iteration))

    return _hook


async def test_cp3_hook_called_per_pass_on_deep_plan():
    """Deep plan → the CP3 hook is visited once per loop pass (per subtopic+iter).

    One DEEP subtopic with a high target: the verifier adds 2 fresh
    UNCORROBORATED claims each pass (never KEPT, never diminishing), so only the
    DEEP budget stops the loop and it runs the full ``MAX_ITER_DEEP`` passes. The
    hook must fire once per pass, in order, with the matching (subtopic.id, iter).
    """
    calls: list[tuple[str, int]] = []
    # Each pass adds 2 NEW claims (no diminishing) and never KEPT (no target-met),
    # so only the DEEP budget stops the loop → exactly ``MAX_ITER_DEEP`` passes.
    budget = config.MAX_ITER_DEEP
    passes = [
        ClaimLedger(
            claims=[
                _uncorroborated_claim(f"c{p}a", "s1"),
                _uncorroborated_claim(f"c{p}b", "s1"),
            ]
        )
        for p in range(budget + 2)
    ]
    verifier = _verifier_stub_from_passes(passes)
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(99, Depth.DEEP),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
        cp3_hook=_recording_cp3_hook(calls),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=99)],
        depth=Depth.DEEP,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    # The hook fired once per pass, in iteration order, all for subtopic s1.
    assert calls == [("s1", i) for i in range(budget)]


async def test_cp3_hook_skipped_on_shallow_plan():
    """Shallow plan → the CP3 hook is NEVER visited (deep-gated no-op)."""
    calls: list[tuple[str, int]] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.SHALLOW),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
        cp3_hook=_recording_cp3_hook(calls),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.SHALLOW,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert calls == [], f"CP3 hook fired on a SHALLOW plan: {calls}"


async def test_cp3_hook_skipped_on_normal_plan():
    """Normal plan → the CP3 hook is NEVER visited (deep-gated no-op)."""
    calls: list[tuple[str, int]] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
        cp3_hook=_recording_cp3_hook(calls),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    assert calls == [], f"CP3 hook fired on a NORMAL plan: {calls}"


# ── T0 gate: the workflow renders a markdown draft from the ledger (CP2 input) ───
async def test_writer_renders_draft_from_ledger():
    """After the loop, the Writer node + ``render_report`` produce a markdown draft
    persisted as ``draft.md`` — the artifact CP2 (T1) will consume.

    The injected canned writer returns a known body; the deterministic skeleton
    (Coverage / Sources sections from ``render_report``) is added on top. The draft
    must contain BOTH the writer's body AND the deterministic sections.
    """
    store = SessionStore.create(RAW_QUERY)
    verifier = _verifier_stub_from_passes(
        [
            ClaimLedger(claims=[_kept_claim("c1", "s1")]),
            ClaimLedger(claims=[_kept_claim("c2", "s2")]),
        ]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
        store=store,
    )
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=1),
            Subtopic(id="s2", question="angle 2", target_evidence=1),
        ],
        depth=Depth.DEEP,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    draft = store.load_draft()
    assert draft is not None, "T0: writer draft was not persisted as draft.md"
    # The Writer's body prose is present...
    assert WRITER_BODY in draft
    # ...and the deterministic render_report sections frame it.
    assert "## Coverage" in draft
    assert "## Sources" in draft
