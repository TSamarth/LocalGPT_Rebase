"""
Pure research-loop policy (driver-free).

Holds the deterministic policy the research loop runs *inside* ``Stage.RESEARCH``:
the phase walker (acquire → [mid-acquire CP3] → extract → verify), the adaptive
per-subtopic budget, the stop-rule, and the pure ledger merge. CP3 only fires on
a deep plan. This module imports only ``schemas`` + ``config`` — no Orchestrator,
no v1 driver bits (``STAGE_ORDER``/``next_stage``) — so the v2 workflow loop can
import it without dragging the v1 stage driver along.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from .config import config
from .schemas import ClaimLedger, Depth, Subtopic


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


# ── Stop-rule + adaptive-depth budget (T4.1 / T4.3, architecture.md §4) ──────────
def depth_budget(depth: Depth) -> int:
    """Per-subtopic pass cap for a plan ``depth`` (T4.3 adaptive budgets).

    Deeper plans earn more Acquire→Extract→Verify passes to reach their higher
    ``target_evidence``. Falls back to the flat cap for any unmapped depth.
    """
    return {
        Depth.SHALLOW: config.MAX_ITER_SHALLOW,
        Depth.NORMAL: config.MAX_ITER_NORMAL,
        Depth.DEEP: config.MAX_ITER_DEEP,
    }.get(depth, config.MAX_ITERATIONS_PER_SUBTOPIC)


def stop_rule(
    ledger: ClaimLedger,
    subtopic: Subtopic,
    *,
    iteration: int,
    new_claims: int,
    budget: int,
) -> bool:
    """Whether the research loop should stop working ``subtopic`` (architecture.md §4).

    Pure + deterministic. ``iteration`` is the number of passes already completed
    for this subtopic, ``new_claims`` the unique claims the most recent pass added,
    ``budget`` the per-subtopic pass cap (see :func:`depth_budget`).

    Stops when ANY holds:
      * target met — corroborated (KEPT) claims ``>= subtopic.target_evidence``;
      * diminishing returns — a completed pass added ``< config.MIN_NEW_CLAIMS``;
      * budget hit — ``iteration >= budget``.
    """
    if ledger.kept_count(subtopic.id) >= subtopic.target_evidence:
        return True
    if iteration >= budget:
        return True
    # Diminishing returns only applies after at least one pass has run.
    if iteration >= 1 and new_claims < config.MIN_NEW_CLAIMS:
        return True
    return False


# ── Pure ledger merge (research-loop accumulation) ───────────────────────────────
def merge_ledger(base: ClaimLedger, new: ClaimLedger) -> ClaimLedger:
    """A new ledger: ``base`` claims followed by ``new`` claims with unseen ids.

    Dedup-by-id, base wins on collision. Inputs are not mutated — the shared
    accumulation primitive for both the v1 pipeline and the v2 loop.
    """
    seen = {c.id for c in base.claims}
    merged = list(base.claims)
    for claim in new.claims:
        if claim.id not in seen:
            merged.append(claim)
            seen.add(claim.id)
    return ClaimLedger(claims=merged)
