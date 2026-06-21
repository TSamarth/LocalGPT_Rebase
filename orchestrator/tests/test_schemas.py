"""Schema contract tests — JSON roundtrip, validation, ledger helpers."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceClass,
    SourceRef,
    Stage,
    StageState,
    Subtopic,
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


def test_stage_state_defaults():
    state = StageState(session_id="sid", raw_query="hello")
    assert state.stage is Stage.INTAKE
    assert state.iteration == 0
    assert state.current_subtopic_id is None
    # enum serialises to its string value
    assert '"intake"' in state.model_dump_json()