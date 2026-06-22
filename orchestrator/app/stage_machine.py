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

from enum import Enum
from typing import Optional

from .schemas import Depth, Stage

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


class ResearchPhase(str, Enum):
    """Sub-phases of one research-loop pass inside ``Stage.RESEARCH``.

    MID_ACQUIRE is the CP3 mid-acquisition checkpoint — it is only visited when
    the plan depth is ``deep`` (FR / CONTEXT.md CP3).
    """
    ACQUIRE = "acquire"
    MID_ACQUIRE = "mid_acquire"  # CP3 — deep plans only
    EXTRACT = "extract"
    VERIFY = "verify"


def first_phase() -> ResearchPhase:
    return ResearchPhase.ACQUIRE


def next_phase(current: ResearchPhase, *, depth: Depth) -> Optional[ResearchPhase]:
    """Next sub-phase within a research pass, or ``None`` when the pass is done.

    ACQUIRE → (MID_ACQUIRE if deep else EXTRACT) → EXTRACT → VERIFY → None.
    Returning ``None`` hands control back to the orchestrator, which applies the
    stop-rule and either re-enters ACQUIRE for another pass or leaves RESEARCH.
    """
    if current == ResearchPhase.ACQUIRE:
        return ResearchPhase.MID_ACQUIRE if depth == Depth.DEEP else ResearchPhase.EXTRACT
    if current == ResearchPhase.MID_ACQUIRE:
        return ResearchPhase.EXTRACT
    if current == ResearchPhase.EXTRACT:
        return ResearchPhase.VERIFY
    return None  # VERIFY → end of pass
