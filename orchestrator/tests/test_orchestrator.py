"""
Checkpoint wiring into the orchestrator (Story 4 T4.2/T4.3, Gate G5).

Exercises ``post_handlers`` (CP1/CP2 on PLAN/WRITE) and ``phase_handlers``
(CP3 on MID_ACQUIRE) — all offline.
"""
from __future__ import annotations

from pathlib import Path

from app import checkpoint
from app.orchestrator import Orchestrator
from app.schemas import CrawlStrategy, Depth, ResearchPlan, ScoredURL, Stage, Subtopic
from app.stage_machine import ResearchPhase


def _plan(depth=Depth.NORMAL):
    return ResearchPlan(
        subtopics=[Subtopic(id="s1", question="q?", target_evidence=1)], depth=depth
    )


def _to_research(orch):
    while orch.stage != Stage.RESEARCH:
        orch.step()


# ── post_handlers (T4.2) ─────────────────────────────────────────────────────
def test_post_handler_fires_after_its_stage():
    fired: list[Stage] = []
    orch = Orchestrator.start("q")
    orch.post_handlers[Stage.PLAN] = lambda o: fired.append(Stage.PLAN)
    orch.post_handlers[Stage.WRITE] = lambda o: fired.append(Stage.WRITE)
    orch.run_to_completion()
    assert fired == [Stage.PLAN, Stage.WRITE]


def test_cp1_handler_persists_edited_plan(monkeypatch):
    orch = Orchestrator.start("q")
    orch.store.save_plan(_plan(depth=Depth.NORMAL))
    orch.post_handlers[Stage.PLAN] = checkpoint.cp1_handler

    inputs = iter(["e", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    monkeypatch.setattr(
        checkpoint, "_launch_editor",
        lambda path: Path(path).write_text(
            _plan(depth=Depth.DEEP).model_dump_json(), encoding="utf-8"
        ),
    )

    _to_research(orch)
    assert orch.store.load_plan().depth == Depth.DEEP


# ── phase_handlers / CP3 (T4.3) ──────────────────────────────────────────────
def test_cp3_skips_on_shallow_plan():
    for depth in (Depth.SHALLOW, Depth.NORMAL):
        orch = Orchestrator.start("q")
        orch.store.save_plan(_plan(depth=depth))
        fired: list[int] = []
        orch.phase_handlers[ResearchPhase.MID_ACQUIRE] = lambda o, f=fired: f.append(1)
        _to_research(orch)
        orch._run_research()
        assert ResearchPhase.MID_ACQUIRE not in orch.phase_trace, (
            f"MID_ACQUIRE must not fire for {depth}"
        )
        assert fired == [], f"MID_ACQUIRE must not fire for {depth}"


def test_cp3_fires_on_deep_plan():
    orch = Orchestrator.start("q")
    orch.store.save_plan(_plan(depth=Depth.DEEP))
    fired: list[int] = []
    orch.phase_handlers[ResearchPhase.MID_ACQUIRE] = lambda o: fired.append(1)
    _to_research(orch)
    orch._run_research()
    assert fired == [1]


def test_cp3_handler_supplemental_reenters_acquire(monkeypatch):
    orch = Orchestrator.start("q")
    orch.store.save_plan(_plan(depth=Depth.DEEP))
    orch.store.save_scored_urls(
        [ScoredURL(url="https://a", score=0.5, strategy=CrawlStrategy.CRAWL)]
    )
    orch.phase_handlers[ResearchPhase.MID_ACQUIRE] = checkpoint.cp3_handler

    inputs = iter(["+ https://new", "d"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    _to_research(orch)
    orch._run_research()

    assert orch.phase_trace.count(ResearchPhase.ACQUIRE) == 2
    assert "https://new" in [u.url for u in orch.store.load_scored_urls()]
