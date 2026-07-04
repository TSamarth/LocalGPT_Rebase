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
from app.workflow import CHECKPOINT_REJECT, build_research_workflow

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


def _build_loop_workflow(*, planner_node, verifier_node):
    """Build a fully-offline workflow with all five nodes injected (canned)."""
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=planner_node,
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier_node,
        writer_node=_writer_stub(),
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
    """Drive the slice to the terminal ledger, approving CP1 with ``approved_plan``.

    Now that CP3 (E3.S1 T2) is a real deep-only ``RequestInput``, a DEEP run pauses
    at CP1 *and* at each per-pass CP3. This helper delegates to
    :func:`_drive_through_checkpoints`, which answers CP1 with the approved plan and
    every CP3 with ``"d"`` (done — no URL edits), so existing CP1/loop tests keep
    their bodies unchanged: CP3 is a transparent no-op pass-through here. Shallow/
    normal runs hit no CP3 pause at all (deep-gated), so they are unaffected.
    """
    return await _drive_through_checkpoints(
        workflow, approved_plan=approved_plan, cp3_reply="d"
    )


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
    # The DEEP plan now pauses at each per-pass CP3 RequestInput after CP1, then
    # once at CP2 after the Writer. We keep resuming (answering each CP3 with
    # ``"d"`` — no URL edits — and CP2 with an EMPTY reply — approve, draft
    # unchanged) until the run produces its terminal ledger. The same
    # ``invocation_id`` threads all resumes.
    final_output = None
    new_message = reply
    for _ in range(100):
        pending = None  # (interrupt_id, reply_payload)
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                if iid.startswith("cp3_"):
                    pending = (iid, {"result": "d"})  # CP3 done, no edits
                else:  # CP2 — approve via empty reply (draft unchanged)
                    pending = (iid, {"result": ""})
            if event.output is not None:
                final_output = event.output
        if pending is None:
            break
        new_message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

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


# ── T2 (E3.S1): real deep-only CP3 RequestInput checkpoint ──────────────────────
#
# CP3 replaced the old ``cp3_hook`` DI seam with a real ``RequestInput`` node wired
# in-loop (deep-only). A DEEP run now pauses at CP1, then once per loop pass at
# ``cp3_{subtopic.id}_{iteration}``. The multi-pause harness below answers CP1 with
# the approved plan and each CP3 with a scripted free-text verb reply, resuming by
# ``invocation_id`` until the terminal ledger is produced.


def _counting_acquirer_stub(counter: list[int]) -> object:
    """Acquirer stub that records each execution (for the supplemental re-entry
    proof) and returns one ScoredURL — same shape as ``_acquirer_stub``."""

    @node
    async def _stub(node_input: str) -> list[ScoredURL]:
        counter.append(1)
        return [
            ScoredURL(
                url="https://a.example/doc",
                score=0.9,
                strategy=CrawlStrategy.CRAWL,
                etld1="a.example",
            )
        ]

    return _stub


def _recording_extractor_stub(seen_urls: list[list[str]]) -> object:
    """Extractor stub that records the URL list it received each pass (so a test
    can assert a user-added URL reached extract). Decodes the JSON node_input."""
    import json as _json

    @node
    async def _stub(node_input: str) -> list[str]:
        payload = _json.loads(node_input)
        seen_urls.append([u["url"] for u in payload])
        return ["page-1"]

    return _stub


async def _drive_through_checkpoints(
    workflow, *, approved_plan: ResearchPlan, cp3_reply, cp2_reply=""
) -> ClaimLedger:
    """Drive a run through CP1 + every per-pass CP3 + CP2 RequestInput to the ledger.

    ``cp3_reply`` is either a fixed reply string applied to every CP3 pause, or a
    callable ``(interrupt_id) -> str`` for per-pass scripting. ``cp2_reply`` is the
    string answered at the (single, unconditional) CP2 draft-approval pause that
    fires after the Writer — default ``""`` (empty = approve, draft unchanged);
    pass a non-empty markdown string to exercise the edit round-trip.

    We loop: run the runner; when it pauses at a RequestInput, dispatch by
    interrupt_id (CP1 → the approved plan dict; ``cp3_*`` → the verb reply string;
    any other UUID id → CP2 → the ``cp2_reply`` string) and resume by
    ``invocation_id``; repeat until a terminal ``event.output`` appears.
    """
    app = _build_app(workflow)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    cp3_interrupts: list[str] = []
    session_state: dict = {}
    cp1_seen = False
    final_output = None
    new_message = message
    invocation_id = None

    for _ in range(200):  # generous cap; real runs need far fewer hops
        pending = None  # (interrupt_id, reply_payload)
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            # Accumulate state_delta so tests can inspect v2_plan/v2_ledger/v2_draft.
            if event.actions and event.actions.state_delta:
                session_state.update(event.actions.state_delta)
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                invocation_id = event.invocation_id
                if iid.startswith("cp3_"):
                    cp3_interrupts.append(iid)
                    reply = cp3_reply(iid) if callable(cp3_reply) else cp3_reply
                    # ``response_schema=str`` → wrap the free-text reply as
                    # ``{"result": <str>}`` (ADK unwraps it back to the str).
                    pending = (iid, {"result": reply})
                elif not cp1_seen:  # CP1 — the FIRST non-cp3_ pause (plan approval)
                    cp1_seen = True
                    pending = (iid, approved_plan.model_dump(mode="json"))
                else:  # CP2 — the draft-approval pause after the Writer
                    pending = (iid, {"result": cp2_reply})
            if event.output is not None:
                final_output = event.output

        if pending is None:
            break  # run reached a terminal output, no further pause
        reply_part = create_request_input_response(pending[0], pending[1])
        new_message = types.Content(role="user", parts=[reply_part])

    assert final_output is not None, "workflow never produced a terminal output"
    # Stash the CP3 interrupt trace and accumulated session state for asserts.
    ledger = ClaimLedger.model_validate(final_output)
    _drive_through_checkpoints.last_cp3_interrupts = cp3_interrupts  # type: ignore[attr-defined]
    _drive_through_checkpoints.last_session_state = session_state  # type: ignore[attr-defined]
    return ledger


async def test_cp3_pauses_once_per_pass_on_deep_plan():
    """Deep plan → CP3 ``RequestInput`` fires once per loop pass with the
    unique per-pass interrupt_id ``cp3_{subtopic.id}_{iteration}``.

    One DEEP subtopic with a high target: the verifier adds 2 fresh
    UNCORROBORATED claims each pass (never KEPT, never diminishing), so only the
    DEEP budget stops the loop → exactly ``MAX_ITER_DEEP`` passes, each pausing
    at CP3. Each CP3 reply is ``d`` (done, no edits)."""
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
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=99)],
        depth=Depth.DEEP,
    )
    await _drive_through_checkpoints(workflow, approved_plan=approve_plan, cp3_reply="d")

    # CP3 paused once per pass, in order, with the matching per-pass interrupt_id.
    assert _drive_through_checkpoints.last_cp3_interrupts == [
        f"cp3_s1_{i}" for i in range(budget)
    ]


async def test_cp3_skipped_on_shallow_plan():
    """Shallow plan → CP3 ``RequestInput`` NEVER fires (deep-gated). The run
    reaches the terminal ledger via CP1 alone (no CP3 pause)."""
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
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.SHALLOW,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="d"
    )
    assert _drive_through_checkpoints.last_cp3_interrupts == [], "CP3 fired on SHALLOW"
    assert result.kept_count("s1") == 1


async def test_cp3_skipped_on_normal_plan():
    """Normal plan → CP3 ``RequestInput`` NEVER fires (deep-gated)."""
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
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="d"
    )
    assert _drive_through_checkpoints.last_cp3_interrupts == [], "CP3 fired on NORMAL"
    assert result.kept_count("s1") == 1


async def test_cp3_exclude_by_index_filters_url_before_extract():
    """CP3 ``- 0`` excludes the acquirer's only URL → extract sees an empty list
    that pass; no supplemental re-entry (exclude does not set supplemental)."""
    seen_urls: list[list[str]] = []
    acquirer_calls: list[int] = []
    # Single DEEP pass: target met after one pass (one KEPT claim).
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.DEEP),
        acquirer_node=_counting_acquirer_stub(acquirer_calls),
        extractor_node=_recording_extractor_stub(seen_urls),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.DEEP,
    )
    await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="- 0\nd"
    )
    # The single URL was excluded → extract saw an empty list; acquirer ran ONCE
    # (no supplemental re-entry on exclude).
    assert seen_urls == [[]]
    assert acquirer_calls == [1]


async def test_cp3_add_triggers_supplemental_acquirer_reentry():
    """CP3 ``+ <url>`` sets supplemental → the acquirer is re-run ONCE in the same
    pass and the user-added URL reaches extract alongside the re-acquired URL."""
    seen_urls: list[list[str]] = []
    acquirer_calls: list[int] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.DEEP),
        acquirer_node=_counting_acquirer_stub(acquirer_calls),
        extractor_node=_recording_extractor_stub(seen_urls),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.DEEP,
    )
    await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="+ https://user-added\nd"
    )
    # Acquirer ran TWICE this pass: initial acquire + one supplemental re-entry.
    assert acquirer_calls == [1, 1]
    # Extract saw the user-added URL (supplemental re-entry merged it in).
    assert len(seen_urls) == 1
    assert "https://user-added" in seen_urls[0]
    # ...alongside the re-acquired candidate.
    assert "https://a.example/doc" in seen_urls[0]


async def test_cp3_redirect_triggers_supplemental_acquirer_reentry():
    """CP3 ``r <url>`` (redirect) also sets supplemental → acquirer re-runs once
    and the steered URL reaches extract."""
    seen_urls: list[list[str]] = []
    acquirer_calls: list[int] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.DEEP),
        acquirer_node=_counting_acquirer_stub(acquirer_calls),
        extractor_node=_recording_extractor_stub(seen_urls),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.DEEP,
    )
    await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="r https://redir\nd"
    )
    assert acquirer_calls == [1, 1]
    assert "https://redir" in seen_urls[0]


# ── T0 gate: the workflow renders a markdown draft from the ledger (CP2 input) ───
async def test_writer_renders_draft_from_ledger():
    """After the loop, the Writer node + ``render_report`` produce a markdown draft
    emitted as ``v2_draft`` in ADK session state — the artifact CP2 (T1) consumes.

    The injected canned writer returns a known body; the deterministic skeleton
    (Coverage / Sources sections from ``render_report``) is added on top. The draft
    must contain BOTH the writer's body AND the deterministic sections.

    The draft is accessed via ``_drive_through_checkpoints.last_session_state``
    (state_delta accumulation) instead of SessionStore, which was removed in E3.S2 T3.
    """
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
    )
    approve_plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="angle 1", target_evidence=1),
            Subtopic(id="s2", question="angle 2", target_evidence=1),
        ],
        depth=Depth.DEEP,
    )
    await _drive_to_cp1_and_resume(workflow, approved_plan=approve_plan)

    draft = _drive_through_checkpoints.last_session_state.get("v2_draft")
    assert draft is not None, "T0: writer draft was not emitted as v2_draft"
    # The Writer's body prose is present...
    assert WRITER_BODY in draft
    # ...and the deterministic render_report sections frame it.
    assert "## Coverage" in draft
    assert "## Sources" in draft


# ── T1 (E3.S1): real CP2 RequestInput draft-approval checkpoint ─────────────────
#
# CP2 is unconditional (runs on EVERY path that reaches the Writer, NOT deep-gated).
# After the Writer renders the draft, the workflow pauses at a CP2 ``RequestInput``;
# the human's reply IS the final draft (empty = approve unchanged, non-empty = the
# edited markdown). The terminal output stays the ``ClaimLedger`` either way; the
# (approved/edited) draft is persisted via ``store.save_draft``.


async def _build_cp2_workflow():
    """Single-subtopic DEEP-free (NORMAL) workflow that reaches the Writer + CP2 in
    one pass, fully offline. NORMAL depth keeps CP3 out of the way so the only
    post-CP1 pause is CP2."""
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )


async def test_cp2_pauses_and_approve_keeps_draft_unchanged():
    """CP2 fires a RequestInput; an EMPTY reply (approve) leaves the draft
    unchanged. The terminal ledger is intact and the approved draft (emitted as
    ``v2_draft`` in session state) equals the Writer-rendered draft (body + skeleton).

    Draft accessed via ``_drive_through_checkpoints.last_session_state`` instead of
    SessionStore, which was removed in E3.S2 T3.
    """
    workflow = await _build_cp2_workflow()
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="d", cp2_reply=""
    )

    # Terminal output is still the accumulated ledger (CP2 approve is ledger-neutral).
    assert isinstance(result, ClaimLedger)
    assert result.kept_count("s1") == 1
    # NORMAL depth → CP3 never fired; the only post-CP1 pause was CP2.
    assert _drive_through_checkpoints.last_cp3_interrupts == []
    # The approved draft is the unchanged Writer render: body + skeleton sections.
    draft = _drive_through_checkpoints.last_session_state.get("v2_draft")
    assert draft is not None
    assert WRITER_BODY in draft
    assert "## Coverage" in draft
    assert "## Sources" in draft


async def test_cp2_edit_roundtrips_to_persisted_draft():
    """CP2 edit: a non-empty reply IS the edited markdown — it round-trips verbatim
    as ``v2_draft`` in session state (replacing the Writer render). The terminal
    ledger is unchanged.

    Draft accessed via ``_drive_through_checkpoints.last_session_state`` instead of
    SessionStore, which was removed in E3.S2 T3.
    """
    workflow = await _build_cp2_workflow()
    approve_plan = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    edited = "# Human-edited report\n\nThis replaces the Writer draft entirely."
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve_plan, cp3_reply="d", cp2_reply=edited
    )

    # Ledger unchanged by the edit.
    assert isinstance(result, ClaimLedger)
    assert result.kept_count("s1") == 1
    # The edited markdown round-trips verbatim as the emitted v2_draft.
    draft = _drive_through_checkpoints.last_session_state.get("v2_draft")
    assert draft == edited
    # ...and the Writer's original body is NOT the approved draft (it was replaced).
    assert WRITER_BODY not in draft


# ── T3 (E3.S1): reject = resumable abort at the rejecting checkpoint ─────────────
#
# A reject reply (the reserved ``CHECKPOINT_REJECT`` sentinel) does NOT crash or
# discard the run: the parent re-issues a FRESH ``RequestInput`` pause at the SAME
# logical checkpoint (new node instance → new run_id/interrupt_id), so the run
# halts there and is resumable by ``invocation_id``. Resuming re-enters that
# checkpoint. This is the ADK-2.3.0-correct analog of v1's ``CheckpointRejected``
# (raised → caught → session left at its last stage, resumable). The mechanism was
# verified against installed ADK 2.3.0: a node that *raises* is recorded
# ``NodeStatus.FAILED`` (terminal, NOT resumable); the ONLY resumable-pause outcome
# is a node that yields a ``RequestInput`` (``NodeStatus.WAITING``). So reject is a
# re-yielded ``RequestInput``, the "rejection/retry cycle" the ADK
# ``RequestInput.interrupt_id`` docstring sanctions.
#
# CP1 reject rides as a schema-valid empty-``subtopics`` plan (ADK validates the
# CP1 resume reply against ``response_schema=ResearchPlan`` first, so a free-form
# sentinel mapping can't be used); CP2/CP3 reject ride as the bare sentinel string
# ``CHECKPOINT_REJECT`` (their schema is ``str``).


async def _drive_scripted(workflow, *, cp1_replies, cp2_replies, cp3_reply="d"):
    """Drive a run, scripting a LIST of replies per checkpoint kind.

    ``cp1_replies`` / ``cp2_replies`` are popped in order at each CP1 / CP2 pause —
    so a reject sentinel followed by a real reply exercises the reject→re-pause→
    approve cycle. Every CP3 pause gets ``cp3_reply``. Returns
    ``(ledger, pause_log)`` where ``pause_log`` is the ordered list of
    ``("cp1"|"cp2"|"cp3", interrupt_id)`` tuples seen — so a test can assert the
    rejecting checkpoint paused AGAIN (a fresh interrupt_id) before completing.
    """
    cp1_q = list(cp1_replies)
    cp2_q = list(cp2_replies)
    app = _build_app(workflow)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    pause_log: list[tuple[str, str]] = []
    final_output = None
    new_message = message
    invocation_id = None

    # CP1 precedes CP2; classify a non-cp3 pause as CP1 while ``cp1_q`` still has
    # scripted replies, else CP2. A reject reply is re-queued implicitly by the
    # workflow re-pausing — the queue simply holds [reject..., real_reply].
    for _ in range(200):
        pending = None  # (interrupt_id, reply_payload)
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                invocation_id = event.invocation_id
                if iid.startswith("cp3_"):
                    pause_log.append(("cp3", iid))
                    pending = (iid, {"result": cp3_reply})
                elif cp1_q:  # CP1 — scripted plan replies (reject sentinel or plan)
                    reply = cp1_q.pop(0)
                    pause_log.append(("cp1", iid))
                    if reply == CHECKPOINT_REJECT:
                        # CP1 reject MUST satisfy response_schema=ResearchPlan, so
                        # the reject signal is a schema-valid empty-subtopics plan
                        # (see app.workflow._is_cp1_reject).
                        pending = (iid, {"subtopics": []})
                    else:
                        pending = (iid, reply)
                else:  # CP2 — scripted str replies (reject sentinel or markdown)
                    reply = cp2_q.pop(0) if cp2_q else ""
                    pause_log.append(("cp2", iid))
                    pending = (iid, {"result": reply})
            if event.output is not None:
                final_output = event.output

        if pending is None:
            break
        new_message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    assert final_output is not None, "workflow never produced a terminal output"
    return ClaimLedger.model_validate(final_output), pause_log


def _single_deep_subtopic_workflow(verifier):
    """A 1-subtopic DEEP workflow reaching CP1 → (per-pass CP3) → Writer → CP2."""
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.DEEP),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )


async def test_cp1_reject_repauses_then_approves():
    """CP1 reject → the run re-pauses at CP1 (a fresh interrupt_id) instead of
    crashing; a follow-up approve drives the loop to the ledger. Two CP1 pauses
    with DISTINCT interrupt_ids prove the resumable-abort re-entry."""
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
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    ledger, pause_log = await _drive_scripted(
        workflow,
        cp1_replies=[CHECKPOINT_REJECT, approve.model_dump(mode="json")],
        cp2_replies=[""],
    )

    cp1_pauses = [iid for kind, iid in pause_log if kind == "cp1"]
    assert len(cp1_pauses) == 2, f"CP1 did not re-pause on reject: {pause_log}"
    assert cp1_pauses[0] != cp1_pauses[1], "reject re-pause reused the interrupt_id"
    # After the approve, the loop ran to a real ledger.
    assert isinstance(ledger, ClaimLedger)
    assert ledger.kept_count("s1") == 1


async def test_cp2_reject_repauses_then_approves():
    """CP2 reject → the run re-pauses at CP2 (fresh interrupt_id), then an empty
    approve completes. Two CP2 pauses with distinct interrupt_ids prove re-entry."""
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
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    ledger, pause_log = await _drive_scripted(
        workflow,
        cp1_replies=[approve.model_dump(mode="json")],
        cp2_replies=[CHECKPOINT_REJECT, ""],
    )

    cp2_pauses = [iid for kind, iid in pause_log if kind == "cp2"]
    assert len(cp2_pauses) == 2, f"CP2 did not re-pause on reject: {pause_log}"
    assert cp2_pauses[0] != cp2_pauses[1], "reject re-pause reused the interrupt_id"
    assert isinstance(ledger, ClaimLedger)
    assert ledger.kept_count("s1") == 1


async def test_cp3_reject_repauses_then_done():
    """CP3 reject (the reserved sentinel) → the deep-only CP3 re-pauses with an
    attempt-suffixed interrupt_id, then a ``d`` (done) reply proceeds. The first
    CP3 pass keeps the original ``cp3_s1_0`` id; the reject retry is
    ``cp3_s1_0_1`` — proving the resumable re-entry at the same checkpoint."""
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = _single_deep_subtopic_workflow(verifier)
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.DEEP,
    )

    # Script CP3: reject on the first sight of cp3_s1_0, then done. Use a callable
    # cp3_reply via the existing multi-pause harness extended for reject.
    seen: list[str] = []

    def cp3_script(iid: str) -> str:
        seen.append(iid)
        # Reject the very first CP3 pause, accept (done) on the re-pause.
        return CHECKPOINT_REJECT if seen.count(iid) == 1 and iid == "cp3_s1_0" else "d"

    ledger = await _drive_through_checkpoints(
        workflow, approved_plan=approve, cp3_reply=cp3_script
    )

    # CP3 paused at the original id AND at the attempt-suffixed retry id.
    assert "cp3_s1_0" in seen
    assert "cp3_s1_0_1" in seen, f"CP3 did not re-pause on reject: {seen}"
    assert isinstance(ledger, ClaimLedger)
    assert ledger.kept_count("s1") == 1


async def test_cp1_reject_does_not_rerun_clarify_or_plan():
    """Port of v1 ``test_cp1_reject_raises`` to the node harness: a CP1 reject leaves
    the upstream (clarify/plan) checkpoint-skipped — they ran exactly ONCE even
    across the reject re-pause (resumable abort, not a full restart)."""
    clarifier_calls: list[int] = []
    planner_calls: list[int] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    # Counting planner forces DEEP w/ 2 subtopics; we approve a 1-subtopic NORMAL
    # plan at CP1 so the loop is small and CP3 stays out of the way.
    workflow = build_research_workflow(
        clarifier_node=_counting_clarifier_stub(clarifier_calls),
        planner_node=_counting_planner_stub(planner_calls),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    _, pause_log = await _drive_scripted(
        workflow,
        cp1_replies=[CHECKPOINT_REJECT, CHECKPOINT_REJECT, approve.model_dump(mode="json")],
        cp2_replies=[""],
    )

    # CP1 paused THREE times (reject, reject, approve) — all distinct ids.
    cp1_pauses = [iid for kind, iid in pause_log if kind == "cp1"]
    assert len(cp1_pauses) == 3
    assert len(set(cp1_pauses)) == 3
    # ...yet clarify/plan ran exactly once across every reject re-pause.
    assert clarifier_calls == [1], f"clarifier re-ran on CP1 reject: {clarifier_calls}"
    assert planner_calls == [1], f"planner re-ran on CP1 reject: {planner_calls}"


# ── E6 P1: acquirer JSON re-ask at the node seam ──────────────────────────────
def _prose_then_json_acquirer_stub(calls: list[str]) -> object:
    """Acquirer that answers in PROSE on the first call (the live failure mode)
    and returns a valid ScoredURL JSON string on the re-ask."""

    @node
    async def _stub(node_input: str) -> str:
        calls.append(node_input)
        if len(calls) == 1:
            return "Vector databases trade recall for latency; in summary..."
        return (
            '[{"url": "https://a.example/doc", "score": 0.9,'
            ' "strategy": "crawl_url", "etld1": "a.example"}]'
        )

    return _stub


def _always_prose_acquirer_stub(calls: list[str]) -> object:
    """Acquirer that never returns JSON — both the first call and the re-ask fail."""

    @node
    async def _stub(node_input: str) -> str:
        calls.append(node_input)
        return "I believe the answer to your question is as follows..."

    return _stub


async def test_acquirer_prose_triggers_json_reask():
    """Prose first answer → workflow re-asks ONCE with the JSON-only suffix and
    the run proceeds on the re-ask's URLs (no silent empty-degrade)."""
    from app.jsonio import JSON_ONLY_REASK

    calls: list[str] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_prose_then_json_acquirer_stub(calls),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve, cp3_reply="d"
    )
    assert len(calls) == 2, f"expected one re-ask, saw {len(calls)} acquirer calls"
    assert calls[1].endswith(JSON_ONLY_REASK)
    assert calls[1].startswith(calls[0])
    assert result.kept_count("s1") == 1
    assert "v2_acquire_failure" not in _drive_through_checkpoints.last_session_state


async def test_acquirer_double_failure_emits_state_event_and_continues():
    """Prose on BOTH attempts → run degrades to zero URLs but the failure is
    surfaced as a ``v2_acquire_failure`` state event, not silently swallowed."""
    calls: list[str] = []
    verifier = _verifier_stub_from_passes(
        [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
    )
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_always_prose_acquirer_stub(calls),
        extractor_node=_extractor_stub(),
        verifier_node=verifier,
        writer_node=_writer_stub(),
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve, cp3_reply="d"
    )
    assert len(calls) == 2  # first call + one bounded re-ask, no infinite loop
    failure = _drive_through_checkpoints.last_session_state.get("v2_acquire_failure")
    assert failure is not None, "acquire failure was silently swallowed"
    assert failure["subtopic_id"] == "s1"
    assert result.kept_count("s1") == 1  # run still completed


# ── E6 P3: verifier handoff seam fails loudly ─────────────────────────────────
def _garbage_verifier_stub() -> object:
    """Verifier that answers in prose → parse_ledger raises ValueError (the live
    failure mode the loud seam must surface)."""

    @node
    async def _stub(node_input: str) -> str:
        return "Based on the sources, the claim appears well supported overall."

    return _stub


async def test_verifier_garbage_emits_failure_state_and_degrades():
    """Unparseable verifier output → empty ledger (as before) BUT a
    ``v2_verify_failure`` state event is emitted instead of a silent swallow."""
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_garbage_verifier_stub(),
        writer_node=_writer_stub(),
    )
    approve = ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.NORMAL,
    )
    result = await _drive_through_checkpoints(
        workflow, approved_plan=approve, cp3_reply="d"
    )
    failure = _drive_through_checkpoints.last_session_state.get("v2_verify_failure")
    assert failure is not None, "verify failure was silently swallowed"
    assert failure["subtopic_id"] == "s1"
    assert result.kept_count("s1") == 0  # degraded to empty ledger, run still completed
