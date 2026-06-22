"""
Checkpoint console flows (Story 4, Gate G5).

All offline: stdin is fed via monkeypatched ``input`` and the editor is replaced
with a fake that writes canned content, so no real editor/Ollama is needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import checkpoint
from app.checkpoint import (
    CheckpointRejected,
    cp1_checkpoint,
    cp2_checkpoint,
    cp3_checkpoint,
)
from app.schemas import CrawlStrategy, Depth, ResearchPlan, ScoredURL, Subtopic


def _feed(monkeypatch, *responses):
    """Make ``input`` return each response in turn."""
    it = iter(responses)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


def _editor_writes(monkeypatch, new_content):
    """Replace the editor so an 'edit' action saves ``new_content``."""
    monkeypatch.setattr(
        checkpoint, "_launch_editor",
        lambda path: Path(path).write_text(new_content, encoding="utf-8"),
    )


def _plan(depth=Depth.NORMAL):
    return ResearchPlan(
        subtopics=[Subtopic(id="s1", question="q?", target_evidence=1)], depth=depth
    )


# ── CP1 ──────────────────────────────────────────────────────────────────────
def test_cp1_blocks_until_approve(monkeypatch):
    plan = _plan()
    _feed(monkeypatch, "a")
    assert cp1_checkpoint(plan) is plan


def test_cp1_edit_roundtrips_through_editor(monkeypatch):
    edited = _plan(depth=Depth.DEEP).model_dump_json()
    _feed(monkeypatch, "e", "a")
    _editor_writes(monkeypatch, edited)
    result = cp1_checkpoint(_plan(depth=Depth.NORMAL))
    assert result.depth == Depth.DEEP


def test_cp1_invalid_edit_keeps_previous(monkeypatch):
    _feed(monkeypatch, "e", "a")
    _editor_writes(monkeypatch, "{not valid json")
    result = cp1_checkpoint(_plan(depth=Depth.NORMAL))
    assert result.depth == Depth.NORMAL


def test_cp1_reject_raises(monkeypatch):
    _feed(monkeypatch, "r")
    with pytest.raises(CheckpointRejected):
        cp1_checkpoint(_plan())


# ── CP2 ──────────────────────────────────────────────────────────────────────
def test_cp2_blocks_until_approve(monkeypatch):
    _feed(monkeypatch, "a")
    assert cp2_checkpoint("# draft") == "# draft"


def test_cp2_edit_roundtrips_through_editor(monkeypatch):
    _feed(monkeypatch, "e", "a")
    _editor_writes(monkeypatch, "# edited draft")
    assert cp2_checkpoint("# draft") == "# edited draft"


def test_cp2_reject_raises(monkeypatch):
    _feed(monkeypatch, "r")
    with pytest.raises(CheckpointRejected):
        cp2_checkpoint("# draft")


# ── CP3 ──────────────────────────────────────────────────────────────────────
def _url(u, score=0.5):
    return ScoredURL(url=u, score=score, strategy=CrawlStrategy.CRAWL, etld1="x.com")


def test_cp3_done_leaves_list_unchanged(monkeypatch):
    urls = [_url("https://a"), _url("https://b")]
    _feed(monkeypatch, "d")
    filtered, supplemental = cp3_checkpoint(urls)
    assert [u.url for u in filtered] == ["https://a", "https://b"]
    assert supplemental is False


def test_cp3_add_sets_supplemental(monkeypatch):
    _feed(monkeypatch, "+ https://new", "d")
    filtered, supplemental = cp3_checkpoint([_url("https://a")])
    assert "https://new" in [u.url for u in filtered]
    assert supplemental is True


def test_cp3_exclude_removes_by_index(monkeypatch):
    _feed(monkeypatch, "- 0", "d")
    filtered, supplemental = cp3_checkpoint([_url("https://a"), _url("https://b")])
    assert [u.url for u in filtered] == ["https://b"]
    assert supplemental is False


def test_cp3_redirect_sets_supplemental(monkeypatch):
    _feed(monkeypatch, "r https://redir", "d")
    filtered, supplemental = cp3_checkpoint([_url("https://a")])
    assert "https://redir" in [u.url for u in filtered]
    assert supplemental is True
