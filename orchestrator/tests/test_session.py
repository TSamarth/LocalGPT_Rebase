"""Session store + resume tests."""
from __future__ import annotations

from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    Depth,
    ResearchPlan,
    Stage,
    Subtopic,
)
from app.session import SessionStore, new_session_id


def _plan() -> ResearchPlan:
    return ResearchPlan(
        subtopics=[Subtopic(id="s1", question="q?", target_evidence=2)],
        depth=Depth.NORMAL,
    )


def test_new_session_id_is_slugged():
    sid = new_session_id("Quantum Computing: an Overview!")
    assert "quantum-computing" in sid
    assert " " not in sid


def test_create_persists_initial_stage():
    store = SessionStore.create("test query")
    state = store.load_stage()
    assert state is not None
    assert state.stage is Stage.INTAKE
    assert state.raw_query == "test query"
    assert state.updated_at  # stamped on save


def test_set_stage_advances_and_carries_state():
    store = SessionStore.create("q")
    store.set_stage(Stage.PLAN, normalized_query="normalized q")
    state = store.load_stage()
    assert state.stage is Stage.PLAN
    assert state.normalized_query == "normalized q"
    assert state.raw_query == "q"  # carried over


def test_plan_and_ledger_roundtrip():
    store = SessionStore.create("q")
    store.save_plan(_plan())
    assert store.load_plan().subtopics[0].id == "s1"

    ledger = ClaimLedger(claims=[
        Claim(id="c1", subtopic_id="s1", text="t", status=ClaimStatus.KEPT),
    ])
    store.save_ledger(ledger)
    assert store.load_ledger().kept_count("s1") == 1


def test_load_ledger_defaults_empty():
    store = SessionStore.create("q")
    assert store.load_ledger().claims == []


def test_resume_existing_session():
    original = SessionStore.create("resume me")
    original.set_stage(Stage.RESEARCH, current_subtopic_id="s1", iteration=2)

    resumed = SessionStore.resume(original.session_id)
    assert resumed is not None
    state = resumed.load_stage()
    assert state.stage is Stage.RESEARCH
    assert state.current_subtopic_id == "s1"
    assert state.iteration == 2


def test_resume_unknown_session_returns_none():
    assert SessionStore.resume("does-not-exist") is None


def test_draft_and_report_write():
    store = SessionStore.create("q")
    store.save_draft("# Draft\n")
    assert store.load_draft() == "# Draft\n"

    path = store.write_report("# Final\n")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Final\n"
    assert path.name == f"{store.session_id}.md"