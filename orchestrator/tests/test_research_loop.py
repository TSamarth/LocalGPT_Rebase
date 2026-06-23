"""
Per-subtopic research loop (Story 5 T4.1).

Drives ``Orchestrator._run_research`` with synthetic phase handlers (no real
agents) to verify the loop mechanics: multi-pass continuation, ledger
accumulation across passes and subtopics, the stop-rule branches, and the
deep-only CP3 gate. All offline.
"""
from __future__ import annotations

from app.config import config
from app.orchestrator import Orchestrator
from app.schemas import (
    Claim,
    ClaimStatus,
    Depth,
    ResearchPlan,
    Subtopic,
)
from app.stage_machine import ResearchPhase


def _plan(depth=Depth.NORMAL, *targets) -> ResearchPlan:
    subs = [
        Subtopic(id=f"s{i}", question="?", target_evidence=t)
        for i, t in enumerate(targets or (3,))
    ]
    return ResearchPlan(subtopics=subs, depth=depth)


def _verify_handler(per_pass: int, status: ClaimStatus = ClaimStatus.KEPT):
    """A fake VERIFY phase that appends ``per_pass`` claims for the current subtopic."""
    def handler(orch: Orchestrator) -> None:
        sub = orch.current_subtopic
        ledger = orch.store.load_ledger()
        base = len(ledger.claims)
        for i in range(per_pass):
            ledger.claims.append(
                Claim(id=f"{sub.id}-{base + i}", subtopic_id=sub.id, text="t", status=status)
            )
        orch.store.save_ledger(ledger)
    return handler


def _start(plan: ResearchPlan, query: str = "q") -> Orchestrator:
    orch = Orchestrator.start(query)
    orch.store.save_plan(plan)
    return orch


def test_loop_stops_when_target_evidence_met():
    # target=4, +2 KEPT per pass → exactly 2 passes, ledger has 4 claims.
    orch = _start(_plan(Depth.NORMAL, 4))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2)
    orch._run_research()
    assert len(orch.store.load_ledger().claims) == 4


def test_loop_accumulates_ledger_across_passes():
    orch = _start(_plan(Depth.NORMAL, 6))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2)
    orch._run_research()
    # 2 KEPT/pass until kept >= 6 → 3 passes.
    assert orch.store.load_ledger().kept_count("s0") == 6


def test_loop_stops_on_diminishing_returns():
    # +1 new claim/pass < MIN_NEW_CLAIMS(2) → stop after the first pass.
    assert config.MIN_NEW_CLAIMS == 2
    orch = _start(_plan(Depth.NORMAL, 5))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(1)
    orch._run_research()
    assert len(orch.store.load_ledger().claims) == 1


def test_loop_stops_on_budget_cap():
    # UNCORROBORATED claims keep new_claims high (no diminishing) but never raise
    # kept_count, so only the depth budget halts the loop.
    orch = _start(_plan(Depth.NORMAL, 99))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2, ClaimStatus.UNCORROBORATED)
    orch._run_research()
    assert len(orch.store.load_ledger().claims) == 2 * config.MAX_ITER_NORMAL


def test_loop_covers_every_subtopic():
    orch = _start(_plan(Depth.NORMAL, 2, 2))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2)
    orch._run_research()
    ledger = orch.store.load_ledger()
    assert ledger.kept_count("s0") == 2
    assert ledger.kept_count("s1") == 2


def test_loop_persists_iteration_pointer():
    orch = _start(_plan(Depth.NORMAL, 4))
    orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2)
    orch._run_research()
    state = orch.store.load_stage()
    assert state.current_subtopic_id == "s0"
    assert state.iteration == 2


def test_cp3_fires_only_on_deep():
    for depth, expected in ((Depth.NORMAL, False), (Depth.DEEP, True)):
        # Distinct query → distinct session dir (session ids are second-grained).
        orch = _start(_plan(depth, 2), query=f"q-{depth.value}")
        fired: list[int] = []
        orch.phase_handlers[ResearchPhase.MID_ACQUIRE] = lambda o, f=fired: f.append(1)
        orch.phase_handlers[ResearchPhase.VERIFY] = _verify_handler(2)
        orch._run_research()
        assert bool(fired) is expected
        assert (ResearchPhase.MID_ACQUIRE in orch.phase_trace) is expected
