"""
Full pipeline integration (Story 5 T4.2) — INTAKE→DONE, offline.

Wires the composition root with canned async runners (no Ollama/MCP) and mocked
checkpoint I/O, then drives the whole stage machine. Verifies the agents +
CP1/CP2/CP3 fire at the right points, the research loop accumulates a ledger, the
report is published, and a rejected checkpoint aborts cleanly.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import config
from app.pipeline import PipelineDeps, build_orchestrator, run_pipeline
from app.schemas import Stage


# ── canned runners ───────────────────────────────────────────────────────────
def _clarify_runner(query: str) -> str:
    return json.dumps({"status": "clear", "normalized_query": f"{query} (norm)", "questions": []})


async def _plan_runner(_query: str, depth: str = "normal") -> str:
    return json.dumps(
        {
            "subtopics": [
                {"id": "s0", "question": "q?", "target_evidence": 2, "source_classes": ["web"]}
            ],
            "depth": depth,
            "seed_urls": [],
            "est_iterations": 1,
        }
    )


async def _acquire_runner(_question: str) -> str:
    return json.dumps(
        [{"url": "https://example.com/a", "score": 0.9, "strategy": "crawl_url", "etld1": "example.com"}]
    )


async def _extract_runner(_urls) -> None:
    return None


async def _verify_runner(_payload: str) -> str:
    # Two independent sources per claim (different eTLD+1) so the Verifier's
    # deterministic policy keeps them KEPT rather than UNCORROBORATED.
    def _sources():
        return [
            {"url": "https://example.com/a", "etld1": "example.com"},
            {"url": "https://other.org/b", "etld1": "other.org"},
        ]

    return json.dumps(
        {
            "claims": [
                {"id": "s0-0", "subtopic_id": "s0", "text": "fact A", "status": "kept",
                 "sources": _sources()},
                {"id": "s0-1", "subtopic_id": "s0", "text": "fact B", "status": "kept",
                 "sources": _sources()},
            ]
        }
    )


async def _write_runner(_plan, _ledger) -> str:
    return "## Findings\n\nfact A and fact B."


def _deps() -> PipelineDeps:
    return PipelineDeps(
        clarify_runner=_clarify_runner,
        plan_runner=_plan_runner,
        acquire_runner=_acquire_runner,
        extract_runner=_extract_runner,
        verify_runner=_verify_runner,
        write_runner=_write_runner,
    )


# ── tests ────────────────────────────────────────────────────────────────────
def test_pipeline_runs_end_to_end_and_publishes_report(monkeypatch):
    # CP1 + CP2 approved (normal depth → no CP3).
    inputs = iter(["a", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    orch = build_orchestrator("test query", deps=_deps())
    final = run_pipeline(orch)

    assert final == Stage.DONE
    # Plan + ledger persisted by the live handlers.
    assert orch.store.load_plan() is not None
    assert orch.store.load_ledger().kept_count("s0") == 2
    # Report published with the deterministic skeleton sections.
    report = Path(config.REPORTS_DIR) / f"{orch.store.session_id}.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "## Coverage" in text and "## Sources" in text
    assert "fact A and fact B." in text


def test_pipeline_normalizes_query_into_plan_stage(monkeypatch):
    inputs = iter(["a", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    orch = build_orchestrator("raw q", deps=_deps())
    run_pipeline(orch)
    assert orch.store.load_stage().normalized_query == "raw q (norm)"


def test_pipeline_aborts_on_rejected_plan(monkeypatch):
    # Reject at CP1 → run aborts, no report, stage left at PLAN.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "r")
    orch = build_orchestrator("test query", deps=_deps())
    final = run_pipeline(orch)

    assert final != Stage.DONE
    assert not (Path(config.REPORTS_DIR) / f"{orch.store.session_id}.md").exists()


def test_pipeline_unattended_without_checkpoints():
    orch = build_orchestrator("test query", deps=_deps(), register_checkpoints=False)
    assert run_pipeline(orch) == Stage.DONE
    assert Stage.PLAN not in orch.post_handlers
