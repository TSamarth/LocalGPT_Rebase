"""
Deterministic stage machine + research-loop sub-phases (Story 3.1).

Top-level stages ARE the frozen ``Stage`` enum (schemas.py) — the inter-agent
contract. The research loop runs *inside* ``Stage.RESEARCH`` as a sequence of
phases (acquire → [mid-acquire CP3] → extract → verify); CP3 only fires on a
deep plan. Phases live here, not in the frozen ``Stage`` enum, so the contract
stays stable while the orchestrator still models the loop + the deep-only
checkpoint (architecture.md §4, CONTEXT.md stage machine).
"""
from __future__ import annotations

from .research_policy import (  # re-exported for v1 imports until E5
    ResearchPhase,
    depth_budget,
    first_phase,
    next_phase,
    stop_rule,
)
from .schemas import Stage

__all__ = [
    "STAGE_ORDER",
    "next_stage",
    "is_terminal",
    "ResearchPhase",
    "first_phase",
    "next_phase",
    "depth_budget",
    "stop_rule",
]

# Canonical linear order of top-level stages. The orchestrator advances strictly
# along this list; nothing skips ahead except resume (which jumps to a saved point).
STAGE_ORDER: list[Stage] = [
    Stage.INTAKE,
    Stage.CLARIFY,
    Stage.PLAN,
    Stage.RESEARCH,
    Stage.SYNTHESIZE,
    Stage.WRITE,
    Stage.DONE,
]


def next_stage(current: Stage) -> Stage:
    """The stage following ``current``; ``DONE`` is a fixed point."""
    if current == Stage.DONE:
        return Stage.DONE
    return STAGE_ORDER[STAGE_ORDER.index(current) + 1]


def is_terminal(stage: Stage) -> bool:
    return stage == Stage.DONE
