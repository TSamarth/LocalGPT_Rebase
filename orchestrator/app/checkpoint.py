"""
Human-in-the-loop checkpoints (Story 4, architecture.md CP1/CP2/CP3).

Three blocking gates let the user inspect and steer the run:

- **CP1** (after Planner) — approve/edit/reject the ``ResearchPlan``.
- **CP2** (after Writer)  — approve/edit/reject the draft report.
- **CP3** (after Acquirer, deep plans only) — inspect the candidate source list,
  add/exclude/redirect URLs before extraction.

Each ``cp*_checkpoint`` function is pure console I/O over a single artifact:
prints to stdout, reads stdin, optionally opens ``$EDITOR`` (``notepad`` on
Windows when ``$EDITOR`` is unset). The ``cp*_handler`` wrappers adapt those to
the orchestrator's ``Handler`` signature — they do the ``self.store`` I/O so the
orchestrator wiring stays a one-liner per stage (T4.2/T4.3).

``_launch_editor`` and ``input``/stdin are the only side effects, so tests drive
the whole flow offline by monkeypatching them.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .schemas import CrawlStrategy, ResearchPlan, ScoredURL
from .stage_machine import ResearchPhase

if TYPE_CHECKING:  # avoid an import cycle; handlers only duck-type the orchestrator
    from .orchestrator import Orchestrator


class CheckpointRejected(Exception):
    """Raised when the user rejects an artifact at a checkpoint. The orchestrator
    catches this and marks the session aborted."""


# ── editor helper ──────────────────────────────────────────────────────────────
def _launch_editor(path: Path) -> None:
    """Open ``path`` in ``$EDITOR`` (``notepad`` on Windows when unset), blocking
    until the editor exits. Monkeypatched in tests to inject edited content."""
    editor = os.environ.get("EDITOR") or "notepad"
    subprocess.run([editor, str(path)], check=False)


def _edit_text(initial: str, suffix: str) -> str:
    """Round-trip ``initial`` through a temp file + ``$EDITOR``; return the saved text."""
    fd, name = tempfile.mkstemp(suffix=suffix)
    path = Path(name)
    try:
        os.close(fd)
        path.write_text(initial, encoding="utf-8")
        _launch_editor(path)
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


# ── CP1 — ResearchPlan ──────────────────────────────────────────────────────────
def cp1_checkpoint(plan: ResearchPlan) -> ResearchPlan:
    """Block until the user approves/edits/rejects the plan. Returns the (possibly
    edited) plan; raises :class:`CheckpointRejected` on reject."""
    while True:
        print("\n=== Checkpoint 1: Research Plan ===")
        print(f"depth={plan.depth.value}  subtopics={len(plan.subtopics)}  "
              f"seed_urls={len(plan.seed_urls)}")
        for st in plan.subtopics:
            print(f"  - [{st.id}] {st.question} (target_evidence={st.target_evidence})")

        choice = input("[a]pprove / [e]dit / [r]eject: ").strip().lower()
        if choice in ("a", "approve"):
            return plan
        if choice in ("r", "reject"):
            raise CheckpointRejected("CP1: plan rejected by user")
        if choice in ("e", "edit"):
            edited = _edit_text(plan.model_dump_json(indent=2), suffix=".json")
            try:
                plan = ResearchPlan.model_validate_json(edited)
            except ValidationError as exc:
                print(f"Invalid plan, keeping previous version:\n{exc}")
            continue
        print("Unrecognized choice.")


# ── CP2 — draft report ──────────────────────────────────────────────────────────
def cp2_checkpoint(draft: str) -> str:
    """Block until the user approves/edits/rejects the draft. Returns the (possibly
    edited) markdown; raises :class:`CheckpointRejected` on reject."""
    while True:
        print("\n=== Checkpoint 2: Draft Report ===")
        print(f"({len(draft)} chars)")

        choice = input("[a]pprove / [e]dit / [r]eject: ").strip().lower()
        if choice in ("a", "approve"):
            return draft
        if choice in ("r", "reject"):
            raise CheckpointRejected("CP2: draft rejected by user")
        if choice in ("e", "edit"):
            draft = _edit_text(draft, suffix=".md")
            continue
        print("Unrecognized choice.")


# ── CP3 — candidate source list (deep plans only) ────────────────────────────────
def cp3_checkpoint(urls: list[ScoredURL]) -> tuple[list[ScoredURL], bool]:
    """Inspect/edit the candidate URL list before extraction.

    Actions: ``+ <url>`` add, ``- <n>`` exclude index ``n``, ``r <url>`` redirect
    (add a steered target), ``d`` done. Returns ``(filtered_urls, needs_supplemental)``
    — ``needs_supplemental`` is ``True`` when the user added/redirected a URL, which
    triggers a re-entry into ACQUIRE so the new targets get crawled."""
    working = list(urls)
    needs_supplemental = False

    while True:
        print("\n=== Checkpoint 3: Candidate Sources ===")
        for i, u in enumerate(working):
            print(f"  [{i}] {u.score:.2f}  {u.etld1 or '?'}  {u.url}")

        action = input("[+] add <url> / [-] exclude <n> / [r]edirect <url> / [d]one: ").strip()
        if not action:
            continue
        verb, _, arg = action.partition(" ")
        verb, arg = verb.lower(), arg.strip()

        if verb in ("d", "done"):
            return working, needs_supplemental
        if verb == "+" and arg:
            working.append(_user_url(arg))
            needs_supplemental = True
        elif verb == "-" and arg.isdigit() and int(arg) < len(working):
            working.pop(int(arg))
        elif verb in ("r", "redirect") and arg:
            working.append(_user_url(arg))
            needs_supplemental = True
        else:
            print("Unrecognized action.")


def _user_url(url: str) -> ScoredURL:
    """A user-forced candidate: max relevance, plain crawl, sourced as ``user``."""
    return ScoredURL(url=url, score=1.0, strategy=CrawlStrategy.CRAWL, source="user")


# ── orchestrator handlers (adapt cp*_checkpoint to the Handler signature) ─────────
def cp1_handler(orch: "Orchestrator") -> None:
    plan = orch.store.load_plan()
    if plan is None:
        return
    orch.store.save_plan(cp1_checkpoint(plan))


def cp2_handler(orch: "Orchestrator") -> None:
    draft = orch.store.load_draft()
    if draft is None:
        return
    orch.store.save_draft(cp2_checkpoint(draft))


def cp3_handler(orch: "Orchestrator") -> None:
    urls = orch.store.load_scored_urls()
    if not urls:
        return
    filtered, needs_supplemental = cp3_checkpoint(urls)
    orch.store.save_scored_urls(filtered)
    if needs_supplemental:
        # Re-enter ACQUIRE so the user's added/redirected targets get crawled.
        orch.phase_trace.append(ResearchPhase.ACQUIRE)
