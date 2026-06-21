"""
Stage machine + orchestrator skeleton (Story 3.1).

These exercise the deterministic transitions, the deep-only CP3 sub-phase, and
resume — all offline (handlers are stubs, no model/Ollama needed).
"""
from __future__ import annotations

from app.orchestrator import Orchestrator
from app.schemas import Depth, ResearchPlan, Stage, Subtopic
from app.session import SessionStore
from app.stage_machine import (
    STAGE_ORDER,
    ResearchPhase,
    first_phase,
    is_terminal,
    next_phase,
    next_stage,
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
    orch._run_research_pass()
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
