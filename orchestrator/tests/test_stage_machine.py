"""
Stage machine + orchestrator skeleton (Story 3.1).

These exercise the deterministic transitions, the deep-only CP3 sub-phase, and
resume — all offline (handlers are stubs, no model/Ollama needed).
"""
from __future__ import annotations

from app.config import config
from app.orchestrator import Orchestrator
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    Depth,
    ResearchPlan,
    Stage,
    Subtopic,
)
from app.stage_machine import (
    STAGE_ORDER,
    ResearchPhase,
    depth_budget,
    first_phase,
    is_terminal,
    next_phase,
    next_stage,
    stop_rule,
)


# ── top-level transitions ────────────────────────────────────────────────────
def test_stage_order_is_linear_to_done():
    seen = [Stage.INTAKE]
    while not is_terminal(seen[-1]):
        seen.append(next_stage(seen[-1]))
    assert seen == STAGE_ORDER
    assert seen[-1] == Stage.DONE


def test_done_is_fixed_point():
    assert next_stage(Stage.DONE) == Stage.DONE
    assert is_terminal(Stage.DONE)


# ── research sub-phases ──────────────────────────────────────────────────────
def _walk_phases(depth: Depth) -> list[ResearchPhase]:
    phases, phase = [], first_phase()
    while phase is not None:
        phases.append(phase)
        phase = next_phase(phase, depth=depth)
    return phases


def test_deep_plan_visits_cp3_mid_acquire():
    phases = _walk_phases(Depth.DEEP)
    assert phases == [
        ResearchPhase.ACQUIRE,
        ResearchPhase.MID_ACQUIRE,
        ResearchPhase.EXTRACT,
        ResearchPhase.VERIFY,
    ]


def test_non_deep_plan_skips_cp3():
    for depth in (Depth.SHALLOW, Depth.NORMAL):
        phases = _walk_phases(depth)
        assert ResearchPhase.MID_ACQUIRE not in phases
        assert phases == [ResearchPhase.ACQUIRE, ResearchPhase.EXTRACT, ResearchPhase.VERIFY]


# ── orchestrator skeleton ────────────────────────────────────────────────────
def test_run_to_completion_reaches_done():
    orch = Orchestrator.start("test query")
    assert orch.run_to_completion() == Stage.DONE
    # Stage persisted, so a fresh handle sees DONE too.
    assert orch.stage == Stage.DONE


def test_handlers_fire_in_order():
    fired: list[Stage] = []
    handlers = {s: (lambda o, s=s: fired.append(s)) for s in Stage if s != Stage.RESEARCH}
    Orchestrator.start("q", handlers=handlers).run_to_completion()
    # Every non-research, non-terminal stage handler ran exactly once, in order.
    assert fired == [Stage.INTAKE, Stage.CLARIFY, Stage.PLAN, Stage.SYNTHESIZE, Stage.WRITE]


def test_research_phase_trace_records_deep_cp3():
    orch = Orchestrator.start("deep q")
    orch.store.save_plan(
        ResearchPlan(subtopics=[Subtopic(id="s1", question="?", target_evidence=1)], depth=Depth.DEEP)
    )
    # Advance to RESEARCH, run the pass, inspect the trace before it moves on.
    while orch.stage != Stage.RESEARCH:
        orch.step()
    orch._run_research()
    assert ResearchPhase.MID_ACQUIRE in orch.phase_trace


def test_resume_continues_from_saved_stage():
    orch = Orchestrator.start("resumable")
    orch.step()  # INTAKE -> CLARIFY
    orch.step()  # CLARIFY -> PLAN
    sid = orch.store.session_id

    resumed = Orchestrator.resume(sid)
    assert resumed.stage == Stage.PLAN
    assert resumed.run_to_completion() == Stage.DONE


def test_resume_unknown_session_raises():
    import pytest

    with pytest.raises(RuntimeError):
        Orchestrator.resume("does-not-exist")


# ── adaptive-depth budgets (T4.3) ────────────────────────────────────────────
def test_depth_budget_scales_with_depth():
    assert depth_budget(Depth.SHALLOW) == config.MAX_ITER_SHALLOW
    assert depth_budget(Depth.NORMAL) == config.MAX_ITER_NORMAL
    assert depth_budget(Depth.DEEP) == config.MAX_ITER_DEEP
    # Deeper plans earn at least as many passes.
    assert depth_budget(Depth.DEEP) >= depth_budget(Depth.NORMAL) >= depth_budget(Depth.SHALLOW)


# ── stop-rule (T4.1) ─────────────────────────────────────────────────────────
def _sub(target: int = 3) -> Subtopic:
    return Subtopic(id="s1", question="?", target_evidence=target)


def _ledger_with_kept(n: int, subtopic_id: str = "s1") -> ClaimLedger:
    return ClaimLedger(
        claims=[
            Claim(id=f"c{i}", subtopic_id=subtopic_id, text="t", status=ClaimStatus.KEPT)
            for i in range(n)
        ]
    )


def test_stop_rule_stops_when_target_evidence_met():
    ledger = _ledger_with_kept(3)
    assert stop_rule(ledger, _sub(3), iteration=1, new_claims=5, budget=4) is True


def test_stop_rule_stops_on_budget_hit():
    # Target not met, but the pass budget is exhausted → stop (runaway guard).
    assert stop_rule(_ledger_with_kept(0), _sub(3), iteration=4, new_claims=5, budget=4) is True


def test_stop_rule_stops_on_diminishing_returns():
    # A completed pass added fewer than MIN_NEW_CLAIMS new claims → stop.
    few = config.MIN_NEW_CLAIMS - 1
    assert stop_rule(_ledger_with_kept(0), _sub(3), iteration=1, new_claims=few, budget=4) is True


def test_stop_rule_continues_when_progressing_and_under_target():
    # Target unmet, budget left, last pass was productive → keep going.
    enough = config.MIN_NEW_CLAIMS
    assert stop_rule(_ledger_with_kept(1), _sub(3), iteration=1, new_claims=enough, budget=4) is False


def test_stop_rule_first_pass_never_diminishing():
    # iteration=0 means no pass has run yet; diminishing-returns must not pre-empt it.
    assert stop_rule(_ledger_with_kept(0), _sub(3), iteration=0, new_claims=0, budget=4) is False
