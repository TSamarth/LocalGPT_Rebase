"""
Pure research-loop policy (driver-free).

These exercise the deterministic phase walker, the deep-only CP3 sub-phase, the
adaptive-depth budget, and the stop-rule — all pure functions, no model/Ollama
and no v1 driver needed.
"""
from __future__ import annotations

from app.config import config
from app.research_policy import (
    ResearchPhase,
    depth_budget,
    first_phase,
    next_phase,
    stop_rule,
)
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    Depth,
    Subtopic,
)


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
