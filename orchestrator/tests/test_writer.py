"""Writer agent tests (T3.7). All offline — the model is never invoked.

Covers: agent construction (markdown, no schema, no tools), the deterministic
coverage check, temporal-drift bucketing, and the rendered appendix structure
(AC7: temporal drift lives in its own section, separate from factual).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.agents.writer import (
    build_writer,
    coverage_report,
    render_report,
    split_contradictions,
)
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    ConflictType,
    Contradiction,
    Depth,
    ResearchPlan,
    SourceRef,
    Subtopic,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def plan() -> ResearchPlan:
    return ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="What is X?", target_evidence=2),
            Subtopic(id="s2", question="What is Y?", target_evidence=2),
        ],
        depth=Depth.NORMAL,
    )


@pytest.fixture
def factual_contradiction() -> Contradiction:
    return Contradiction(
        url="https://factual.example/a",
        etld1="example",
        quote="X is actually Z.",
        confidence_score=0.8,
        conflict_type=ConflictType.FACTUAL,
    )


@pytest.fixture
def drift_contradiction() -> Contradiction:
    return Contradiction(
        url="https://drift.example/old",
        etld1="example",
        quote="X used to be W (2015).",
        publication_date=date(2015, 1, 1),
        confidence_score=0.4,
        conflict_type=ConflictType.TEMPORAL_DRIFT,
    )


@pytest.fixture
def ledger(factual_contradiction, drift_contradiction) -> ClaimLedger:
    """s1 has claims (one factual + one temporal-drift contradiction); s2 has none."""
    return ClaimLedger(
        claims=[
            Claim(
                id="c1",
                subtopic_id="s1",
                text="X is a thing.",
                status=ClaimStatus.KEPT,
                sources=[SourceRef(url="https://src.example/x", quote="X is a thing.")],
                contradictions=[factual_contradiction],
            ),
            Claim(
                id="c2",
                subtopic_id="s1",
                text="X has evolved over the years.",
                status=ClaimStatus.KEPT,
                sources=[SourceRef(url="https://src.example/x2")],
                contradictions=[drift_contradiction],
            ),
        ]
    )


# ── build_writer ──────────────────────────────────────────────────────────────
def test_build_writer_is_markdown_agent_no_schema_no_tools():
    agent = build_writer()
    assert agent.name == "writer"
    assert agent.instruction.strip(), "writer must have a non-empty role prompt"
    # Reasoning-only markdown agent: no forced JSON schema, no tools.
    assert agent.output_schema is None
    assert not agent.tools


# ── coverage_report ───────────────────────────────────────────────────────────
def test_coverage_flags_missing_subtopic(plan, ledger):
    report = coverage_report(plan, ledger)
    assert "s1" in report["covered"]
    assert "s2" in report["missing"]
    assert report["fully_covered"] is False
    assert report["per_subtopic"]["s2"]["total_claims"] == 0
    assert report["per_subtopic"]["s1"]["total_claims"] == 2


def test_coverage_fully_covered_when_all_subtopics_have_claims(plan):
    full = ClaimLedger(
        claims=[
            Claim(id="a", subtopic_id="s1", text="x", status=ClaimStatus.KEPT),
            Claim(id="b", subtopic_id="s2", text="y", status=ClaimStatus.KEPT),
        ]
    )
    report = coverage_report(plan, full)
    assert report["missing"] == []
    assert report["fully_covered"] is True


# ── split_contradictions ──────────────────────────────────────────────────────
def test_split_routes_temporal_drift_into_its_own_bucket(ledger):
    buckets = split_contradictions(ledger)
    factual_urls = [c.url for _, c in buckets["factual"]]
    drift_urls = [c.url for _, c in buckets["temporal_drift"]]

    assert "https://drift.example/old" in drift_urls
    assert "https://drift.example/old" not in factual_urls
    assert "https://factual.example/a" in factual_urls
    assert "https://factual.example/a" not in drift_urls
    # Each bucket exists even when methodological is empty.
    assert buckets["methodological"] == []


def test_split_sorts_each_bucket_by_confidence_desc():
    high = Contradiction(url="hi", confidence_score=0.9, conflict_type=ConflictType.FACTUAL)
    low = Contradiction(url="lo", confidence_score=0.2, conflict_type=ConflictType.FACTUAL)
    led = ClaimLedger(
        claims=[
            Claim(
                id="c",
                subtopic_id="s1",
                text="t",
                status=ClaimStatus.FLAGGED,
                contradictions=[low, high],
            )
        ]
    )
    ordered = [c.url for _, c in split_contradictions(led)["factual"]]
    assert ordered == ["hi", "lo"]


# ── render_report ─────────────────────────────────────────────────────────────
def test_rendered_report_separates_temporal_drift_from_factual(plan, ledger):
    md = render_report(plan, ledger, body_markdown="## What is X?\nBody prose.")

    # Both appendix sub-sections present and distinct.
    assert "### Factual & Methodological Contradictions" in md
    assert "### Temporal Drift" in md

    drift_idx = md.index("### Temporal Drift")
    factual_idx = md.index("### Factual & Methodological Contradictions")
    assert factual_idx < drift_idx, "factual section must precede the drift section"

    # The drift contradiction is listed AFTER the temporal-drift header, and the
    # factual one BEFORE it — i.e. each lands in its own sub-section.
    drift_section = md[drift_idx:]
    factual_section = md[factual_idx:drift_idx]
    assert "https://drift.example/old" in drift_section
    assert "https://drift.example/old" not in factual_section
    assert "https://factual.example/a" in factual_section
    assert "https://factual.example/a" not in drift_section


def test_rendered_report_surfaces_coverage_gap(plan, ledger):
    md = render_report(plan, ledger)
    assert "## Coverage" in md
    assert "Coverage gap" in md
    assert "`s2`" in md  # the missing subtopic is named


def test_rendered_report_includes_body_and_sources(plan, ledger):
    body = "## What is X?\nDetailed prose about X."
    md = render_report(plan, ledger, body_markdown=body)
    assert "Detailed prose about X." in md
    assert "## Sources" in md
    assert "https://src.example/x" in md


def test_rendered_report_with_empty_ledger_still_well_formed(plan):
    md = render_report(plan, ClaimLedger())
    assert "# Research Report" in md
    assert "### Temporal Drift" in md
    assert "_No temporal drift detected._" in md
    assert "_No cited sources._" in md
