"""
Deterministic local metrics for the E5.S2.T1 live acceptance harness.

All functions are pure Python — no LLM judge, no network, no Ollama.
Each returns ``{"pass": bool, "reason": str}``.
"""
from __future__ import annotations

from app.schemas import ClaimLedger, ClaimStatus, ConflictType


# ---------------------------------------------------------------------------
# AC3 — structural section check
# ---------------------------------------------------------------------------

_AC3_REQUIRED_SECTIONS = [
    "## Coverage",
    "## Contradictions",
    "### Factual & Methodological Contradictions",
    "### Temporal Drift",
    "## Sources",
]


def check_ac3_sections(draft: str) -> dict:
    """Assert the rendered markdown contains all required section headers.

    These sections are emitted deterministically by ``render_report``; their
    presence proves the deterministic report skeleton ran end-to-end.
    """
    missing = [s for s in _AC3_REQUIRED_SECTIONS if s not in draft]
    if missing:
        return {
            "pass": False,
            "reason": f"Missing section(s): {missing}",
        }
    return {"pass": True, "reason": "All required sections present"}


# ---------------------------------------------------------------------------
# AC4 — contradictory claim check
# ---------------------------------------------------------------------------


def check_ac4_contradiction(ledger: ClaimLedger) -> dict:
    """Assert the ledger contains at least one FLAGGED claim."""
    flagged = [c for c in ledger.claims if c.status == ClaimStatus.FLAGGED]
    if not flagged:
        return {
            "pass": False,
            "reason": (
                f"No FLAGGED claims found in ledger "
                f"(statuses: {[c.status for c in ledger.claims]})"
            ),
        }
    return {
        "pass": True,
        "reason": f"{len(flagged)} FLAGGED claim(s) found",
    }


# ---------------------------------------------------------------------------
# AC5 — uncorroborated claim check
# ---------------------------------------------------------------------------


def check_ac5_uncorroborated(ledger: ClaimLedger) -> dict:
    """Assert the ledger contains at least one UNCORROBORATED claim."""
    uncorr = [c for c in ledger.claims if c.status == ClaimStatus.UNCORROBORATED]
    if not uncorr:
        return {
            "pass": False,
            "reason": (
                f"No UNCORROBORATED claims found in ledger "
                f"(statuses: {[c.status for c in ledger.claims]})"
            ),
        }
    return {
        "pass": True,
        "reason": f"{len(uncorr)} UNCORROBORATED claim(s) found",
    }


# ---------------------------------------------------------------------------
# AC7 — temporal drift check
# ---------------------------------------------------------------------------

_TEMPORAL_DRIFT_SECTION = "### Temporal Drift"
_TEMPORAL_DRIFT_EMPTY = "_No temporal drift detected._"


def check_ac7_temporal_drift(ledger: ClaimLedger, draft: str) -> dict:
    """Assert temporal_drift conflict_type is present in ledger AND rendered
    in the Temporal Drift section (not mixed into the Factual section).

    Two sub-checks:
    1. At least one contradiction with ``conflict_type == temporal_drift`` exists.
    2. The ``### Temporal Drift`` section in the draft is non-empty (i.e. does NOT
       contain only the "no temporal drift" placeholder).
    """
    # Sub-check 1: ledger has at least one temporal_drift contradiction
    drift_pairs = [
        (c, contra)
        for c in ledger.claims
        for contra in c.contradictions
        if contra.conflict_type == ConflictType.TEMPORAL_DRIFT
    ]
    if not drift_pairs:
        return {
            "pass": False,
            "reason": (
                "No contradiction with conflict_type=temporal_drift found in ledger"
            ),
        }

    # Sub-check 2: draft has a non-empty Temporal Drift section
    if _TEMPORAL_DRIFT_SECTION not in draft:
        return {
            "pass": False,
            "reason": f"Section '{_TEMPORAL_DRIFT_SECTION}' not found in draft",
        }

    # Extract the content under ### Temporal Drift (everything until next ## heading)
    td_start = draft.index(_TEMPORAL_DRIFT_SECTION) + len(_TEMPORAL_DRIFT_SECTION)
    # Find the next heading at ## level (but not ###)
    remainder = draft[td_start:]
    next_h2 = remainder.find("\n## ")
    if next_h2 != -1:
        td_content = remainder[:next_h2]
    else:
        td_content = remainder

    if _TEMPORAL_DRIFT_EMPTY in td_content:
        return {
            "pass": False,
            "reason": (
                "Temporal Drift section exists but contains only the empty placeholder; "
                "expected actual drift entries"
            ),
        }

    return {
        "pass": True,
        "reason": (
            f"{len(drift_pairs)} temporal_drift contradiction(s) found in ledger; "
            "Temporal Drift section is non-empty in draft"
        ),
    }
