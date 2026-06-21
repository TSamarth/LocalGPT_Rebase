"""
Writer agent (Story 3.7, FR6) — turns a vetted ``ClaimLedger`` + ``ResearchPlan``
into a structured markdown research report.

The Writer is reasoning-only: it holds no tools and emits **markdown free text**,
never JSON. We therefore build it with ``build_agent`` WITHOUT an ``output_schema``
(an output schema would force ADK into structured-JSON mode — architecture.md §3.7,
§2 "least-privilege tool access": Writer is reasoning-only on artifacts).

Two report properties must be guaranteed, not left to the model:

* **Coverage** (FR6.3) — every plan subtopic must appear in the report; gaps are
  surfaced explicitly. :func:`coverage_report` computes this deterministically.
* **Temporal drift** (FR5.6, §12.2, AC7) — the contradictions appendix splits
  ``conflict_type == temporal_drift`` items into their own sub-section, distinct
  from factual contradictions. :func:`split_contradictions` buckets them; the
  appendix is rendered from those buckets.

Because these are deterministic, the report *skeleton* (:func:`render_report`) is
pure Python and fully testable without a live model. The model only fills the
prose body, which is injected via ``body_markdown`` — keeping model invocation
out of the structural guarantees.
"""
from __future__ import annotations

from typing import Dict, List

from google.adk.agents import LlmAgent

from ..llm import build_agent
from ..schemas import (
    Claim,
    ClaimLedger,
    ConflictType,
    Contradiction,
    ResearchPlan,
)

__all__ = [
    "WRITER_ROLE_PROMPT",
    "build_writer",
    "coverage_report",
    "split_contradictions",
    "render_report",
]


# ── Role prompt ───────────────────────────────────────────────────────────────
WRITER_ROLE_PROMPT = """\
You are the WRITER agent in a multi-agent research pipeline. Your sole inputs are
a ResearchPlan (the subtopics the report must cover) and a ClaimLedger (every
vetted claim, each already tagged kept / flagged / uncorroborated by the Verifier).
You write the final report; you do NOT do new research, browse, or invent facts.

Produce a single, well-structured Markdown report with this shape:

1. `# <Title>` and a short `## Executive Summary` synthesising the findings.
2. One `## <Subtopic>` section per plan subtopic, IN PLAN ORDER. Cover EVERY
   subtopic. If a subtopic has no claims, say so explicitly under a
   `> Coverage gap:` note rather than omitting the section.
3. Within each section, write prose grounded ONLY in the ledger's claims:
   - Every KEPT claim you state MUST cite its source URL(s) inline, e.g.
     "... (source: https://example.com)". Never assert a kept claim without a source.
   - Mark UNCORROBORATED claims explicitly as single-source / unverified.
   - Where claims disagree (FLAGGED), surface the disagreement; do not silently pick a side.
4. A `## Contradictions` appendix at the end with TWO clearly separated sub-sections:
   - `### Factual & Methodological Contradictions` — genuine disagreements.
   - `### Temporal Drift` — cases where sources differ only because the field
     evolved over time. Frame these as informational ("the field has evolved
     since <date>"), NOT as alarming conflicts. Keep them OUT of the factual list.
5. A `## Sources` list of all cited URLs.

Be precise, neutral, and traceable. Prefer "according to <source>" attribution.
Output ONLY the Markdown report — no preamble, no JSON, no code fences around the
whole document.
"""


# ── Agent factory ─────────────────────────────────────────────────────────────
def build_writer(*, output_key: str = "report_markdown") -> LlmAgent:
    """Build the reasoning-only Writer agent.

    No ``output_schema`` (markdown free text, not JSON) and no tools — the Writer
    operates purely on the artifacts handed to it. ``output_key`` names the
    session-state slot the rendered markdown lands in.
    """
    return build_agent(
        name="writer",
        role_prompt=WRITER_ROLE_PROMPT,
        output_key=output_key,
    )


# ── Deterministic coverage check (FR6.3) ──────────────────────────────────────
def coverage_report(plan: ResearchPlan, ledger: ClaimLedger) -> Dict[str, object]:
    """Verify every plan subtopic has at least one claim in the ledger.

    Returns a dict with:
      * ``covered``      — subtopic ids that have >= 1 claim,
      * ``missing``      — subtopic ids with zero claims (coverage gaps),
      * ``per_subtopic`` — id → {question, total_claims, kept, complete} detail,
      * ``fully_covered``— True iff there are no missing subtopics.

    Deterministic and model-free; the report builder surfaces this explicitly so
    gaps can never be hidden by prose.
    """
    covered: List[str] = []
    missing: List[str] = []
    per_subtopic: Dict[str, Dict[str, object]] = {}

    for sub in plan.subtopics:
        claims = ledger.for_subtopic(sub.id)
        kept = ledger.kept_count(sub.id)
        total = len(claims)
        if total > 0:
            covered.append(sub.id)
        else:
            missing.append(sub.id)
        per_subtopic[sub.id] = {
            "question": sub.question,
            "total_claims": total,
            "kept": kept,
            # "complete" = met the planner's evidence target for this subtopic.
            "complete": kept >= sub.target_evidence,
        }

    return {
        "covered": covered,
        "missing": missing,
        "per_subtopic": per_subtopic,
        "fully_covered": len(missing) == 0,
    }


# ── Deterministic contradiction bucketing (FR5.6, §12.2) ──────────────────────
def split_contradictions(
    ledger: ClaimLedger,
) -> Dict[str, List[tuple[Claim, Contradiction]]]:
    """Bucket every contradiction in the ledger by ``conflict_type``.

    Returns ``{"factual": [...], "methodological": [...], "temporal_drift": [...]}``
    where each entry is a ``(claim, contradiction)`` pair so the appendix can show
    which claim each contradiction attaches to. Within each bucket, entries are
    sorted by ``confidence_score`` descending (architecture.md §12.1).
    """
    buckets: Dict[str, List[tuple[Claim, Contradiction]]] = {
        ConflictType.FACTUAL.value: [],
        ConflictType.METHODOLOGICAL.value: [],
        ConflictType.TEMPORAL_DRIFT.value: [],
    }
    for claim in ledger.claims:
        for contra in claim.contradictions:
            buckets[contra.conflict_type.value].append((claim, contra))

    for entries in buckets.values():
        entries.sort(key=lambda pair: pair[1].confidence_score, reverse=True)
    return buckets


def _render_contradiction_line(claim: Claim, contra: Contradiction) -> str:
    bits = [f"- Claim `{claim.id}`: {claim.text.strip()}"]
    tail = []
    if contra.url:
        tail.append(f"contradicting source: {contra.url}")
    if contra.publication_date is not None:
        tail.append(f"dated {contra.publication_date.isoformat()}")
    tail.append(f"confidence {contra.confidence_score:.2f}")
    if contra.quote:
        tail.append(f'"{contra.quote.strip()}"')
    return bits[0] + " (" + "; ".join(tail) + ")"


def _render_appendix(ledger: ClaimLedger) -> str:
    buckets = split_contradictions(ledger)
    factual = buckets[ConflictType.FACTUAL.value]
    methodological = buckets[ConflictType.METHODOLOGICAL.value]
    drift = buckets[ConflictType.TEMPORAL_DRIFT.value]

    lines: List[str] = ["## Contradictions", ""]

    # Factual + methodological share the "genuine disagreement" sub-section.
    lines.append("### Factual & Methodological Contradictions")
    genuine = factual + methodological
    if genuine:
        lines.extend(_render_contradiction_line(c, x) for c, x in genuine)
    else:
        lines.append("_No factual or methodological contradictions detected._")
    lines.append("")

    # Temporal drift ALWAYS gets its own, clearly separate sub-section (AC7).
    lines.append("### Temporal Drift")
    if drift:
        lines.append(
            "_Informational: the following reflect how the field evolved over "
            "time rather than genuine factual conflicts._"
        )
        lines.extend(_render_contradiction_line(c, x) for c, x in drift)
    else:
        lines.append("_No temporal drift detected._")
    lines.append("")

    return "\n".join(lines)


def _render_coverage_section(report: Dict[str, object]) -> str:
    lines: List[str] = ["## Coverage", ""]
    per: Dict[str, Dict[str, object]] = report["per_subtopic"]  # type: ignore[assignment]
    for sub_id, detail in per.items():
        status = "covered" if detail["total_claims"] else "MISSING — coverage gap"
        lines.append(
            f"- `{sub_id}` ({detail['question']}): {status} — "
            f"{detail['total_claims']} claim(s), {detail['kept']} kept"
        )
    if report["missing"]:
        lines.append("")
        lines.append(
            "> Coverage gap: no claims gathered for subtopic(s): "
            + ", ".join(f"`{m}`" for m in report["missing"])  # type: ignore[arg-type]
        )
    lines.append("")
    return "\n".join(lines)


# ── Deterministic report skeleton ─────────────────────────────────────────────
def render_report(
    plan: ResearchPlan,
    ledger: ClaimLedger,
    body_markdown: str = "",
    *,
    title: str = "Research Report",
) -> str:
    """Assemble the final markdown report skeleton, model-free.

    Structure: title → Coverage (explicit, from :func:`coverage_report`) →
    model-written body (``body_markdown``, may be empty in tests) →
    Contradictions appendix (factual vs. temporal-drift sub-sections) → Sources.

    The deterministic parts (coverage, appendix, sources) are guaranteed here so
    the structural acceptance criteria never depend on the LLM. ``body_markdown``
    is whatever prose the Writer agent produced for the per-subtopic sections.
    """
    cov = coverage_report(plan, ledger)

    parts: List[str] = [f"# {title}", ""]
    parts.append(_render_coverage_section(cov))

    if body_markdown.strip():
        parts.append(body_markdown.strip())
        parts.append("")

    parts.append(_render_appendix(ledger))
    parts.append(_render_sources(ledger))

    return "\n".join(parts).rstrip() + "\n"


def _render_sources(ledger: ClaimLedger) -> str:
    lines: List[str] = ["## Sources", ""]
    seen: List[str] = []
    for claim in ledger.claims:
        for src in claim.sources:
            if src.url and src.url not in seen:
                seen.append(src.url)
    if seen:
        lines.extend(f"- {url}" for url in seen)
    else:
        lines.append("_No cited sources._")
    return "\n".join(lines)
