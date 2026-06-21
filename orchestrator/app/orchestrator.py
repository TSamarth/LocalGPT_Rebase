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
from .schemas import Depth, Stage
from .session import SessionStore
from .stage_machine import (
    ResearchPhase,
    first_phase,
    is_terminal,
    next_phase,
    next_stage,
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
        # Visited research sub-phases of the current pass (CP3 lands here on deep).
        self.phase_trace: list[ResearchPhase] = []

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
            self._run_research_pass()
        else:
            self.handlers[current](self)

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
    def _run_research_pass(self) -> None:
        """One acquire→[mid-acquire CP3]→extract→verify pass.

        Skeleton runs a single pass; the stop-rule + budget caps (T5.1) decide
        multi-pass continuation later. The phase trace records whether CP3 fired,
        which the deep-only checkpoint gate (G5) is verified against.
        """
        depth = self._plan_depth()
        self.phase_trace = []
        phase: Optional[ResearchPhase] = first_phase()
        while phase is not None:
            self.phase_trace.append(phase)
            phase = next_phase(phase, depth=depth)

    def _plan_depth(self) -> Depth:
        plan = self.store.load_plan()
        return plan.depth if plan is not None else Depth.NORMAL
