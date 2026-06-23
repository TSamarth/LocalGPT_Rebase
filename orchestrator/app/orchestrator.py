"""
Root orchestrator — deterministic driver over the stage machine (Story 3.1).

The orchestrator owns ALL stage transitions, persistence and resume; agents only
return artifacts. Each top-level stage maps to a *handler* (a callable taking the
orchestrator). Handlers default to import-tolerant stubs so this skeleton runs
end-to-end before any agent lands; later stories replace a stub by registering
the real agent for its stage. Resume reads ``stage.json`` and continues from the
saved stage — completed stages are never re-run (architecture.md §4–§5).
"""
from __future__ import annotations

from typing import Callable, Optional

from .config import config
from .schemas import Depth, Stage, Subtopic
from .session import SessionStore
from .stage_machine import (
    ResearchPhase,
    depth_budget,
    first_phase,
    is_terminal,
    next_phase,
    next_stage,
    stop_rule,
)

# A handler does a stage's work (call agent, persist artifact). The skeleton's
# stubs do nothing but let the machine advance; real agents replace them.
Handler = Callable[["Orchestrator"], None]


def _stub(_: "Orchestrator") -> None:
    """No-op placeholder until the stage's agent is wired in."""
    return None


class Orchestrator:
    """Walks a session through the stage machine, persisting after each step."""

    def __init__(self, store: SessionStore, handlers: Optional[dict[Stage, Handler]] = None):
        self.store = store
        # Per-stage handlers; every stage defaults to a stub so the skeleton runs.
        self.handlers: dict[Stage, Handler] = {stage: _stub for stage in Stage}
        if handlers:
            self.handlers.update(handlers)
        # Post-stage handlers — fire after a stage's work (CP1 on PLAN, CP2 on WRITE).
        self.post_handlers: dict[Stage, Handler] = {}
        # Research sub-phase handlers — fire per phase (acquire/extract/verify +
        # CP3 on MID_ACQUIRE). Phase handlers read ``self.current_subtopic`` to know
        # which subtopic they operate on, and communicate via ``self.store``.
        self.phase_handlers: dict[ResearchPhase, Handler] = {}
        # Visited research sub-phases across the RESEARCH stage (CP3 lands here on
        # deep; the deep-only gate G5 is verified against this trace).
        self.phase_trace: list[ResearchPhase] = []
        # Subtopic the research loop is currently working (None outside RESEARCH).
        self.current_subtopic: Optional[Subtopic] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    @classmethod
    def start(cls, query: str, handlers: Optional[dict[Stage, Handler]] = None) -> "Orchestrator":
        config.ensure_data_dirs()
        return cls(SessionStore.create(query), handlers)

    @classmethod
    def resume(cls, session_id: str, handlers: Optional[dict[Stage, Handler]] = None) -> "Orchestrator":
        store = SessionStore.resume(session_id)
        if store is None:
            raise RuntimeError(f"no resumable session: {session_id}")
        return cls(store, handlers)

    @property
    def stage(self) -> Stage:
        state = self.store.load_stage()
        if state is None:
            raise RuntimeError(f"session {self.store.session_id} has no stage.json")
        return state.stage

    # ── stepping ─────────────────────────────────────────────────────────────
    def step(self) -> Stage:
        """Run the current stage's handler, then advance + persist. Returns the
        new stage. No-op once terminal."""
        current = self.stage
        if is_terminal(current):
            return current

        if current == Stage.RESEARCH:
            self._run_research()
        else:
            self.handlers[current](self)

        self.post_handlers.get(current, _stub)(self)
        advanced = next_stage(current)
        self.store.set_stage(advanced)
        return advanced

    def run_to_completion(self, max_steps: int = 100) -> Stage:
        """Advance until DONE (or the safety bound trips)."""
        for _ in range(max_steps):
            if is_terminal(self.stage):
                break
            self.step()
        return self.stage

    # ── research loop (inside Stage.RESEARCH) ─────────────────────────────────
    def _run_research(self) -> None:
        """Per-subtopic, budget-bounded research loop (T4.1, architecture.md §4).

        For each plan subtopic, run acquire→[mid-acquire CP3]→extract→verify passes
        until :func:`stop_rule` fires (target_evidence met, diminishing returns, or
        the depth-scaled per-subtopic budget is hit). The ledger accumulates across
        passes and subtopics; ``phase_trace`` records every visited phase so the
        deep-only CP3 gate (G5) is verifiable.
        """
        plan = self.store.load_plan()
        if plan is None:
            return  # nothing planned (skeleton/stub run) — leave RESEARCH cleanly
        depth = plan.depth
        budget = depth_budget(depth)
        self.phase_trace = []

        for subtopic in plan.subtopics:
            self.current_subtopic = subtopic
            iteration = 0
            new_claims = 0
            while not stop_rule(
                self.store.load_ledger(),
                subtopic,
                iteration=iteration,
                new_claims=new_claims,
                budget=budget,
            ):
                before = len(self.store.load_ledger().claims)
                self._run_one_pass(depth)
                after = len(self.store.load_ledger().claims)
                new_claims = after - before
                iteration += 1
                self.store.set_stage(
                    Stage.RESEARCH,
                    current_subtopic_id=subtopic.id,
                    iteration=iteration,
                )

        self.current_subtopic = None

    def _run_one_pass(self, depth: Depth) -> None:
        """Walk one acquire→[mid-acquire CP3]→extract→verify pass over the phase
        handlers. CP3 (MID_ACQUIRE) is only visited on deep plans (next_phase gate)."""
        phase: Optional[ResearchPhase] = first_phase()
        while phase is not None:
            self.phase_trace.append(phase)
            self.phase_handlers.get(phase, _stub)(self)
            phase = next_phase(phase, depth=depth)
