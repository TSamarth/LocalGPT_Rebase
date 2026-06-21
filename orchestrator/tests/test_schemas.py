"""Schema contract tests — JSON roundtrip, validation, ledger helpers."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    ConflictType,
    Contradiction,
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceClass,
    SourceRef,
    Stage,
    StageState,
    Subtopic,
    TemporalStatus,
)


def test_research_plan_roundtrip():
    plan = ResearchPlan(
        subtopics=[
            Subtopic(id="s1", question="What is X?", target_evidence=3,
                     source_classes=[SourceClass.WEB, SourceClass.ACADEMIC]),
        ],
        depth=Depth.DEEP,
        seed_urls=["https://example.com/seed"],
        est_iterations=2,
    )
    restored = ResearchPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.subtopics[0].source_classes == [SourceClass.WEB, SourceClass.ACADEMIC]


def test_subtopic_target_evidence_must_be_positive():
    with pytest.raises(ValidationError):
        Subtopic(id="s1", question="q", target_evidence=0)


def test_scored_url_strategy_enum():
    url = ScoredURL(url="https://a.com", score=0.8, strategy=CrawlStrategy.ADAPTIVE,
                    source="duckduckgo", also_in=["arxiv"], etld1="a.com")
    assert url.strategy is CrawlStrategy.ADAPTIVE
    with pytest.raises(ValidationError):
        ScoredURL(url="x", score=0.1, strategy="not_a_strategy")


def test_claim_ledger_helpers():
    ledger = ClaimLedger(claims=[
        Claim(id="c1", subtopic_id="s1", text="kept claim", status=ClaimStatus.KEPT,
              sources=[SourceRef(url="https://a.com", etld1="a.com", quote="q1"),
                       SourceRef(url="https://b.org", etld1="b.org", quote="q2")]),
        Claim(id="c2", subtopic_id="s1", text="single source", status=ClaimStatus.UNCORROBORATED,
              sources=[SourceRef(url="https://a.com", etld1="a.com")]),
        Claim(id="c3", subtopic_id="s2", text="other subtopic", status=ClaimStatus.KEPT),
    ])
    assert len(ledger.for_subtopic("s1")) == 2
    assert ledger.kept_count("s1") == 1   # only c1 is KEPT under s1
    assert ledger.kept_count("s2") == 1
    assert ledger.kept_count("nope") == 0


def test_empty_ledger_default():
    assert ClaimLedger().claims == []


# ── T0.4 schema extension ─────────────────────────────────────────────────────
def test_t04_optional_fields_default_absent():
    """New T0.4 fields default to null/empty — keeps pre-T0.4 artifacts valid."""
    url = ScoredURL(url="https://a.com", score=0.5, strategy=CrawlStrategy.CRAWL)
    assert url.publication_date is None
    assert url.citation_refs == []
    claim = Claim(id="c1", subtopic_id="s1", text="t", status=ClaimStatus.KEPT)
    assert claim.temporal_status is None
    assert claim.contradictions == []


def test_t04_scored_url_date_roundtrip():
    url = ScoredURL(
        url="https://arxiv.org/abs/x", score=0.9, strategy=CrawlStrategy.DEEP,
        publication_date=date(2024, 3, 1), citation_refs=["S2:abc", "arXiv:1234"],
    )
    restored = ScoredURL.model_validate_json(url.model_dump_json())
    assert restored == url
    assert restored.publication_date == date(2024, 3, 1)
    assert restored.citation_refs == ["S2:abc", "arXiv:1234"]


def test_t04_contradiction_scoring_roundtrip():
    claim = Claim(
        id="c1", subtopic_id="s1", text="X improves Y", status=ClaimStatus.FLAGGED,
        temporal_status=TemporalStatus.DATED,
        sources=[SourceRef(url="https://old.com", etld1="old.com",
                           publication_date=date(2020, 1, 1))],
        contradictions=[Contradiction(
            url="https://new.org", etld1="new.org", quote="X no longer improves Y",
            publication_date=date(2024, 1, 1), confidence_score=0.8,
            conflict_type=ConflictType.TEMPORAL_DRIFT,
        )],
    )
    restored = Claim.model_validate_json(claim.model_dump_json())
    assert restored == claim
    assert restored.contradictions[0].conflict_type is ConflictType.TEMPORAL_DRIFT
    assert restored.contradictions[0].confidence_score == 0.8
    assert restored.temporal_status is TemporalStatus.DATED


def test_t04_contradiction_confidence_bounds():
    with pytest.raises(ValidationError):
        Contradiction(url="x", confidence_score=1.5)


def test_t04_legacy_contradiction_json_still_loads():
    """Pre-T0.4 contradiction record (url/etld1/quote only) validates with defaults."""
    legacy = '{"url":"https://b.org","etld1":"b.org","quote":"q"}'
    c = Contradiction.model_validate_json(legacy)
    assert c.confidence_score == 0.0
    assert c.conflict_type is ConflictType.FACTUAL
    assert c.publication_date is None


def test_stage_state_defaults():
    state = StageState(session_id="sid", raw_query="hello")
    assert state.stage is Stage.INTAKE
    assert state.iteration == 0
    assert state.current_subtopic_id is None
    # enum serialises to its string value
    assert '"intake"' in state.model_dump_json()
