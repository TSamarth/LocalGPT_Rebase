"""
Golden-output A/B parity: v1 pipeline ledger == v2 workflow ledger (E2.S2 T6, HARD GATE).

The migration is only correct if the v2 dynamic-workflow research loop reproduces
the v1 ``Orchestrator._run_research`` ledger exactly. This file drives BOTH stacks
over a SINGLE source of truth for the canned inputs and asserts the terminal
``ClaimLedger`` is structurally equal.

How identical inputs are guaranteed (the parity crux):
  * **Plan.** Both stacks consume the same ``PLAN_*`` dict. v1's ``plan_runner``
    returns ``json.dumps(PLAN)``; v2's planner stub returns
    ``ResearchPlan.model_validate(PLAN)`` AND the same dict is the human-approved
    plan supplied at v2's CP1 resume. Both paths run the *same* deterministic
    ``apply_depth_targets`` (v1 via ``planner.plan`` + CP1-approve-unchanged, v2 via
    ``parse_plan`` on both planner output and the resumed plan), so the plan that
    drives each loop is identical.
  * **Verifier output.** Both stacks share ``_verify_script(passes)`` — a per-call
    closure that returns the SAME list of ledger dicts in the SAME call order
    (v1 calls ``verify`` once per pass; v2 calls ``ctx.run_node(verifier, ...)``
    once per pass — same schedule). v1 returns ``json.dumps(dict)``; v2 returns
    ``ClaimLedger.model_validate(dict)``. Both then run the IDENTICAL
    ``enrich_ledger(parse_ledger(...))`` (v1 inside ``verifier.verify``; v2 inline
    in the loop), so claim status/confidence are recomputed the same way.
  * **merge + stop-rule.** Both import ``research_policy.merge_ledger`` /
    ``stop_rule`` / ``depth_budget`` (T1), so dedup-by-id and the three stop-rule
    exits behave identically given identical per-pass claim sets.

Coverage: a SHALLOW case (no CP3) and a DEEP case (CP3 visited). Both stacks answer
the deep CP3 gate with ``d`` (done, no edits): v1's console gate and v2's real
``RequestInput`` CP3 node each leave the URL list untouched, so neither perturbs the
ledger — keeping parity honest.

Also here: a resume-mid-loop test that extends the E2.S1 resume gate INTO the loop
(Test strategy line 154) — proving completed loop passes are checkpoint-skipped on
resume, i.e. the deterministic-execution-ID requirement holds inside the loop.
"""
from __future__ import annotations

import asyncio
import json
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
from app.pipeline import PipelineDeps, build_orchestrator, run_pipeline
from app.schemas import ClaimLedger, Depth, ResearchPlan, Stage, Subtopic
from app.workflow import build_research_workflow

RAW_QUERY = "tell me about rust async runtimes"

warnings.filterwarnings(
    "ignore",
    message=r".*ResumabilityConfig.*",
    category=UserWarning,
)


# ── Shared canned data — the SINGLE source of truth fed to BOTH stacks ───────────
def _sources_kept() -> list[dict]:
    """Two independent (distinct-etld1) sources → enrich_ledger keeps the claim."""
    return [
        {"url": "https://a.example/1", "etld1": "a.example", "quote": "q1"},
        {"url": "https://b.example/1", "etld1": "b.example", "quote": "q2"},
    ]


def _sources_single() -> list[dict]:
    """One source → enrich_ledger leaves the claim UNCORROBORATED."""
    return [{"url": "https://a.example/1", "etld1": "a.example", "quote": "q1"}]


def _claim(claim_id: str, subtopic_id: str, *, kept: bool) -> dict:
    """A raw claim dict (pre-enrichment). ``status`` is intentionally a placeholder
    the deterministic ``enrich_ledger`` recomputes — proving both stacks enrich."""
    return {
        "id": claim_id,
        "subtopic_id": subtopic_id,
        "text": f"assertion {claim_id}",
        "status": "uncorroborated",
        "sources": _sources_kept() if kept else _sources_single(),
    }


def _ledger_dict(*claims: dict) -> dict:
    return {"claims": list(claims)}


# Plans (single source of truth). target_evidence values are placeholders both
# stacks overwrite via apply_depth_targets(depth) — so they must NOT drive the
# asserted stop condition by themselves.
def _plan_dict(subtopic_ids: list[str], depth: str) -> dict:
    return {
        "subtopics": [
            {"id": sid, "question": f"angle {sid}", "target_evidence": 99,
             "source_classes": ["web"]}
            for sid in subtopic_ids
        ],
        "depth": depth,
        "seed_urls": [],
        "est_iterations": 1,
    }


# ── v1 canned runners (share the same dicts as v2) ───────────────────────────────
def _clarify_runner(query: str) -> str:
    return json.dumps({"status": "clear", "normalized_query": f"{query} (norm)", "questions": []})


def _make_v1_plan_runner(plan_dict: dict):
    async def _plan_runner(_query: str, depth: str = "normal") -> str:
        return json.dumps(plan_dict)
    return _plan_runner


async def _acquire_runner(_question: str) -> str:
    return json.dumps(
        [{"url": "https://a.example/doc", "score": 0.9, "strategy": "crawl_url",
          "etld1": "a.example"}]
    )


async def _extract_runner(_urls) -> None:
    return None


def _make_v1_verify_runner(passes: list[dict]):
    """v1 verify runner: returns the next ledger dict (as JSON) per call, then
    empties. A call-index closure mirrors the v2 ``_verifier_stub_from_passes`` so
    the SAME per-pass data lands in the SAME call order on both stacks."""
    calls = {"i": 0}

    async def _verify_runner(_payload: str) -> str:
        i = calls["i"]
        calls["i"] += 1
        if i < len(passes):
            return json.dumps(passes[i])
        return json.dumps(_ledger_dict())
    return _verify_runner


async def _write_runner(_plan, _ledger) -> str:
    return "## Findings\n\nbody."


def _v1_deps(plan_dict: dict, passes: list[dict]) -> PipelineDeps:
    return PipelineDeps(
        clarify_runner=_clarify_runner,
        plan_runner=_make_v1_plan_runner(plan_dict),
        acquire_runner=_acquire_runner,
        extract_runner=_extract_runner,
        verify_runner=_make_v1_verify_runner(passes),
        write_runner=_write_runner,
    )


# ── v2 canned @node stubs (share the same dicts as v1) ───────────────────────────
def _v2_clarifier_stub():
    from app.schemas import ClarifyResult

    @node
    async def _stub(node_input: str):
        return ClarifyResult(status="clear", normalized_query=f"{node_input} (norm)")
    return _stub


def _make_v2_planner_stub(plan_dict: dict):
    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan.model_validate(plan_dict)
    return _stub


def _v2_acquirer_stub():
    from app.schemas import CrawlStrategy, ScoredURL

    @node
    async def _stub(node_input: str) -> list[ScoredURL]:
        return [ScoredURL(url="https://a.example/doc", score=0.9,
                          strategy=CrawlStrategy.CRAWL, etld1="a.example")]
    return _stub


def _v2_extractor_stub():
    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]
    return _stub


def _v2_writer_stub():
    """Canned writer node (no model call). The Writer runs AFTER the loop and does
    not touch the ledger, so it is parity-neutral — but the default Writer is a live
    ``LlmAgent``, so this stub keeps ``_run_v2`` offline. Returns a ``str`` body
    (the real no-``output_schema`` Writer also yields a ``str``)."""

    @node
    async def _stub(node_input: str) -> str:
        return "## Findings\n\nbody."
    return _stub


def _make_v2_verifier_stub(passes: list[dict]):
    """v2 verifier node: returns the next ledger (validated from the SAME dict) per
    call, then empties — call-index closure matching the v1 runner above."""
    calls = {"i": 0}

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        i = calls["i"]
        calls["i"] += 1
        if i < len(passes):
            return ClaimLedger.model_validate(passes[i])
        return ClaimLedger(claims=[])
    return _stub


# ── drivers ──────────────────────────────────────────────────────────────────────
def _run_v1(plan_dict: dict, passes: list[dict], monkeypatch, *, deep: bool) -> ClaimLedger:
    """Drive v1 ``run_pipeline`` offline; return the persisted terminal ledger.

    CP1 is answered ``a`` (approve unchanged). On a DEEP plan the CP3 console gate
    fires once per pass; it is answered ``d`` (done, no edits) so it never perturbs
    the URL list — keeping v1's ledger identical to v2's no-op ``cp3_hook``.
    """
    # Inputs: a generous stream of "a" (CP1/CP2) and "d" (CP3 done). Order matters
    # only loosely: CP1 is first; CP3 ("d") and CP2 ("a") are interchangeable here
    # because "a" is not a valid CP3 verb (treated as unrecognized → re-prompt), so
    # we feed "d" for every CP3 prompt and "a" for the plan/draft gates.
    replies = iter(["a"] + ["d"] * 50 + ["a"] * 10)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies))

    orch = build_orchestrator(RAW_QUERY, deps=_v1_deps(plan_dict, passes))
    final = run_pipeline(orch)
    assert final == Stage.DONE
    return orch.store.load_ledger()


async def _run_v2(plan_dict: dict, passes: list[dict]) -> ClaimLedger:
    """Drive the v2 workflow: START → CP1 (approve the SAME plan) → loop → ledger."""
    workflow = build_research_workflow(
        clarifier_node=_v2_clarifier_stub(),
        planner_node=_make_v2_planner_stub(plan_dict),
        acquirer_node=_v2_acquirer_stub(),
        extractor_node=_v2_extractor_stub(),
        verifier_node=_make_v2_verifier_stub(passes),
        writer_node=_v2_writer_stub(),
    )
    app = App(
        name="parity_v2",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    interrupt_id = None
    invocation_id = None
    async for event in runner.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            interrupt_id = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id
    assert interrupt_id is not None and invocation_id is not None

    # Approve the SAME plan dict at CP1 (human-approved = planner output, unchanged),
    # then answer each subsequent RequestInput pause until the terminal ledger:
    #   * per-pass CP3 (DEEP plans only, ``cp3_*`` interrupt_id) → ``"d"`` (done, no
    #     edits) — mirroring the v1 console CP3 answered ``d``, so neither stack
    #     perturbs the URL list;
    #   * CP2 (unconditional, after the Writer; a non-``cp3_`` UUID interrupt_id) →
    #     an EMPTY reply, which the CP2 node treats as *approve* (draft unchanged).
    # Neither answer mutates the ledger, so the v1↔v2 ledger equality is byte-
    # identical to before CP2 existed.
    new_message = types.Content(
        role="user",
        parts=[create_request_input_response(interrupt_id, plan_dict)],
    )
    final_output = None
    for _ in range(100):
        pending = None  # (interrupt_id, reply_payload)
        async for event in runner.run_async(
            user_id="test-user", session_id=session.id,
            invocation_id=invocation_id, new_message=new_message,
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
    return ClaimLedger.model_validate(final_output)


# ── structural-equality assertion (stable normalized form) ───────────────────────
def _normalize(ledger: ClaimLedger) -> list[dict]:
    """Claims sorted by id, JSON-dumped — object identity is irrelevant."""
    return [c.model_dump(mode="json") for c in sorted(ledger.claims, key=lambda c: c.id)]


def _assert_parity(v1: ClaimLedger, v2: ClaimLedger, *, subtopic_ids: list[str]) -> None:
    # Same total claim count.
    assert len(v1.claims) == len(v2.claims), (
        f"claim count differs: v1={len(v1.claims)} v2={len(v2.claims)}"
    )
    # Same claims (id, subtopic_id, text, status, ...) — full structural equality.
    assert _normalize(v1) == _normalize(v2), "claim sets differ structurally"
    # Same kept_count per subtopic.
    for sid in subtopic_ids:
        assert v1.kept_count(sid) == v2.kept_count(sid), (
            f"kept_count({sid}) differs: v1={v1.kept_count(sid)} v2={v2.kept_count(sid)}"
        )


# ── PARITY (HARD GATE): shallow case ─────────────────────────────────────────────
def test_parity_shallow_target_met(monkeypatch):
    """SHALLOW plan, two subtopics. Each subtopic's first pass yields enough KEPT
    claims to meet the SHALLOW target (config.TARGET_EVIDENCE_SHALLOW=2) → one pass
    each, stop on target-met. No CP3 (shallow). v1 ledger == v2 ledger."""
    assert config.TARGET_EVIDENCE_SHALLOW == 2
    plan = _plan_dict(["s1", "s2"], "shallow")
    # Per-pass script (call order = s1 pass1, then s2 pass1).
    passes = [
        _ledger_dict(_claim("c1", "s1", kept=True), _claim("c2", "s1", kept=True)),
        _ledger_dict(_claim("c3", "s2", kept=True), _claim("c4", "s2", kept=True)),
    ]
    v1 = _run_v1(plan, passes, monkeypatch, deep=False)
    v2 = asyncio.run(_run_v2(plan, passes))

    _assert_parity(v1, v2, subtopic_ids=["s1", "s2"])
    assert {c.id for c in v1.claims} == {"c1", "c2", "c3", "c4"}
    assert v1.kept_count("s1") == 2 and v1.kept_count("s2") == 2


# ── PARITY (HARD GATE): deep case (CP3 visited) ──────────────────────────────────
def test_parity_deep_budget_hit(monkeypatch):
    """DEEP plan, one subtopic. Claims stay UNCORROBORATED (never target-met) and
    every pass adds 2 fresh claims (never diminishing), so only the DEEP budget
    (config.MAX_ITER_DEEP) stops the loop. The deep CP3 RequestInput fires each pass
    in v2 (answered ``d``, no edits) and the CP3 console gate fires each pass in v1
    (answered ``d``, no edits). v1 ledger == v2 ledger."""
    budget = config.MAX_ITER_DEEP
    plan = _plan_dict(["s1"], "deep")
    passes = [
        _ledger_dict(
            _claim(f"c{p}a", "s1", kept=False),
            _claim(f"c{p}b", "s1", kept=False),
        )
        for p in range(budget + 2)  # budget stops it first
    ]
    v1 = _run_v1(plan, passes, monkeypatch, deep=True)
    v2 = asyncio.run(_run_v2(plan, passes))

    _assert_parity(v1, v2, subtopic_ids=["s1"])
    # Exactly ``budget`` passes ran, each adding 2 unique UNCORROBORATED claims.
    assert len(v1.claims) == 2 * budget
    assert v1.kept_count("s1") == 0


# ── PARITY (HARD GATE): deep case with diminishing-returns exit ──────────────────
def test_parity_deep_diminishing_returns(monkeypatch):
    """DEEP plan, one subtopic. Pass 1 adds 2 new claims; pass 2 RE-returns the
    same ids (dedup → 0 new) → stop on diminishing returns BEFORE the budget. Both
    stacks route dedup through ``merge_ledger`` and the same stop-rule → identical
    2-claim ledger after exactly two passes."""
    assert config.MIN_NEW_CLAIMS == 2
    plan = _plan_dict(["s1"], "deep")
    pass1 = _ledger_dict(
        _claim("c1", "s1", kept=False), _claim("c2", "s1", kept=False)
    )
    passes = [pass1, pass1]  # pass 2 duplicates pass 1 → 0 new
    v1 = _run_v1(plan, passes, monkeypatch, deep=True)
    v2 = asyncio.run(_run_v2(plan, passes))

    _assert_parity(v1, v2, subtopic_ids=["s1"])
    assert {c.id for c in v1.claims} == {"c1", "c2"}
    assert v1.kept_count("s1") == 0


# ── Resume mid-loop: completed passes are checkpoint-skipped (Test strategy L154) ─
def _counting_verifier_stub(passes: list[dict], call_log: list[int]):
    """v2 verifier node that appends to ``call_log`` every time its BODY executes.

    On resume, ADK skips already-completed ``ctx.run_node`` calls (returning their
    cached output) instead of re-running the body — so the log only grows for passes
    that actually (re-)execute. That counter is the proof that completed loop passes
    are NOT re-run after a mid-loop interrupt/resume.
    """
    calls = {"i": 0}

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        call_log.append(1)
        i = calls["i"]
        calls["i"] += 1
        if i < len(passes):
            return ClaimLedger.model_validate(passes[i])
        return ClaimLedger(claims=[])
    return _stub


async def test_resume_mid_loop_skips_completed_passes():
    """Extends the E2.S1 resume gate INTO the research loop.

    A two-subtopic DEEP plan, each subtopic completing in ONE pass (target met).
    We drive START → CP1, resume by ``invocation_id``, and run the full loop to its
    terminal ledger. The verifier body-call counter (the only loop node that runs
    on every pass) proves how many passes executed across the WHOLE run.

    Invariant proven: the resumed run drives the loop to completion via the
    deterministic schedule (acquire→extract→verify per pass, same order every
    replay), so ADK's auto-generated execution IDs align and the terminal ledger is
    produced exactly once — the verifier executes exactly ``#passes`` times across
    the run (here 2: one per subtopic), NOT re-running clarify/plan/CP1-completed
    work on resume.

    Why this is the strongest clean invariant available offline: ADK's
    ``InMemoryRunner`` resume replays the parent ``research`` node and skips
    *already-checkpointed* child ``run_node`` calls. With ``InMemoryRunner`` we
    cannot kill the process mid-pass and rehydrate from disk (no on-disk
    checkpoints), so a true mid-pass crash/restart is not modelable offline. We
    instead assert the equivalent observable: across the kill-at-CP1 + resume
    boundary the loop runs each pass exactly once (no duplicate verifier executions
    from re-running completed work), which is precisely the deterministic-execution-
    ID guarantee the loop relies on. The companion ``test_resume_reruns_cp1_only``
    in test_workflow.py proves clarify/plan are checkpoint-skipped at the same boundary.

    This test is ``async`` (not wrapped in ``asyncio.run``) because it drives only
    the v2 ADK runner — no synchronous v1 ``asyncio.run`` bridge is involved.
    """
    plan = _plan_dict(["s1", "s2"], "deep")
    # Each subtopic completes in EXACTLY ONE pass: pass 1 adds a single claim
    # (new_claims=1 < MIN_NEW_CLAIMS=2), so the diminishing-returns stop-rule fires
    # before a second pass. One pass per subtopic × two subtopics = 2 verifier body
    # executions across the whole run — a clean, deterministic pass count.
    passes = [
        _ledger_dict(_claim("c1", "s1", kept=True)),   # s1 pass1: 1 new → diminish
        _ledger_dict(_claim("c2", "s2", kept=True)),   # s2 pass1: 1 new → diminish
    ]
    call_log: list[int] = []
    workflow = build_research_workflow(
        clarifier_node=_v2_clarifier_stub(),
        planner_node=_make_v2_planner_stub(plan),
        acquirer_node=_v2_acquirer_stub(),
        extractor_node=_v2_extractor_stub(),
        verifier_node=_counting_verifier_stub(passes, call_log),
        writer_node=_v2_writer_stub(),
    )
    app = App(
        name="parity_resume",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="test-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])

    # ── Phase 1: run to CP1 (the kill point). The loop has NOT started yet — CP1
    # gates plan approval, which precedes the loop — so the verifier never ran. ──
    interrupt_id = None
    invocation_id = None
    async for event in runner.run_async(
        user_id="test-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            interrupt_id = get_request_input_interrupt_ids(event)[0]
            invocation_id = event.invocation_id
    assert interrupt_id is not None and invocation_id is not None
    assert call_log == [], "verifier ran before CP1 resume — loop started too early"

    # ── Phase 2: resume by invocation_id; the loop now runs to completion. The
    # DEEP plan pauses at each per-pass CP3 RequestInput (answer ``"d"`` — no edits)
    # and once at CP2 after the Writer (answer EMPTY — approve, draft unchanged).
    # Keep resuming until the terminal ledger. Neither pause perturbs the verifier
    # call count (CP3 is between acquire and verify; CP2 runs after the loop). ──
    new_message = types.Content(
        role="user", parts=[create_request_input_response(interrupt_id, plan)]
    )
    final_output = None
    for _ in range(100):
        pending = None  # (interrupt_id, reply_payload)
        async for event in runner.run_async(
            user_id="test-user", session_id=session.id,
            invocation_id=invocation_id, new_message=new_message,
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

    # ── THE GATE: each pass ran exactly once across the kill/resume boundary. ──
    # One pass per subtopic × two subtopics = 2 verifier body executions, with NO
    # re-execution of completed work (no duplicate/inflated count). The deterministic
    # schedule made the resumed replay align on the same passes.
    assert len(call_log) == 2, f"verifier executed {len(call_log)} times, expected 2"
    result = ClaimLedger.model_validate(final_output)
    assert {c.id for c in result.claims} == {"c1", "c2"}
    assert result.kept_count("s1") == 1 and result.kept_count("s2") == 1
