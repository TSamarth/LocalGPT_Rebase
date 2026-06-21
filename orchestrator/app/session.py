"""
Session store + resume (architecture.md §5).

A run's durable state lives in data/sessions/{session_id}/:
    stage.json         — StageState pointer (where the run is)
    plan.json          — ResearchPlan (after Checkpoint 1)
    claim_ledger.json  — ClaimLedger (grows through the research loop)
    draft.md           — synthesized report (before Checkpoint 2)
The final approved report is written to data/reports/{session_id}.md.

Resume: load_stage() returns the StageState if a run exists; the orchestrator
reads it, skips completed stages, and re-enters the loop. Crawled content
already lives in the MCP's SQLite/ChromaDB, so nothing is re-crawled.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Optional

from .config import config
from .schemas import ClaimLedger, ResearchPlan, Stage, StageState


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _slugify(text: str, max_len: int = 40) -> str:
    """Short filesystem-safe slug from the query, for human-readable session ids."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "query"


def new_session_id(query: str) -> str:
    """Timestamped + slugged id, e.g. 20260619-143000_quantum-computing."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{_slugify(query)}"


class SessionStore:
    """File-backed persistence for one research session. Pure I/O — no logic."""

    STAGE_FILE = "stage.json"
    PLAN_FILE = "plan.json"
    LEDGER_FILE = "claim_ledger.json"
    DRAFT_FILE = "draft.md"

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.dir = Path(config.SESSIONS_DIR) / session_id
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── factory / resume ───────────────────────────────────────────────────
    @classmethod
    def create(cls, raw_query: str) -> "SessionStore":
        """Start a fresh session and persist its initial INTAKE stage."""
        store = cls(new_session_id(raw_query))
        store.save_stage(StageState(session_id=store.session_id, raw_query=raw_query))
        return store

    @classmethod
    def resume(cls, session_id: str) -> Optional["SessionStore"]:
        """Reopen an existing session; None if it has no stage.json."""
        store = cls(session_id)
        if store.load_stage() is None:
            return None
        return store

    # ── stage pointer ──────────────────────────────────────────────────────
    def save_stage(self, state: StageState) -> None:
        state.updated_at = _now_iso()
        self._write(self.STAGE_FILE, state.model_dump_json(indent=2))

    def load_stage(self) -> Optional[StageState]:
        raw = self._read(self.STAGE_FILE)
        return StageState.model_validate_json(raw) if raw is not None else None

    def set_stage(self, stage: Stage, **fields) -> StageState:
        """Advance the stage pointer, carrying over existing state."""
        state = self.load_stage()
        if state is None:
            raise RuntimeError(f"session {self.session_id} has no stage.json")
        state.stage = stage
        for key, value in fields.items():
            setattr(state, key, value)
        self.save_stage(state)
        return state

    # ── plan ─────────────────────────────────────────────────────────────────
    def save_plan(self, plan: ResearchPlan) -> None:
        self._write(self.PLAN_FILE, plan.model_dump_json(indent=2))

    def load_plan(self) -> Optional[ResearchPlan]:
        raw = self._read(self.PLAN_FILE)
        return ResearchPlan.model_validate_json(raw) if raw is not None else None

    # ── claim ledger ───────────────────────────────────────────────────────
    def save_ledger(self, ledger: ClaimLedger) -> None:
        self._write(self.LEDGER_FILE, ledger.model_dump_json(indent=2))

    def load_ledger(self) -> ClaimLedger:
        """Returns an empty ledger if none persisted yet (loop appends to it)."""
        raw = self._read(self.LEDGER_FILE)
        return ClaimLedger.model_validate_json(raw) if raw is not None else ClaimLedger()

    # ── draft + final report ───────────────────────────────────────────────
    def save_draft(self, markdown: str) -> None:
        self._write(self.DRAFT_FILE, markdown)

    def load_draft(self) -> Optional[str]:
        return self._read(self.DRAFT_FILE)

    def write_report(self, markdown: str) -> Path:
        """Write the final approved report to data/reports/{session_id}.md."""
        reports = Path(config.REPORTS_DIR)
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"{self.session_id}.md"
        path.write_text(markdown, encoding="utf-8")
        return path

    # ── low-level I/O ──────────────────────────────────────────────────────
    def _write(self, name: str, content: str) -> None:
        (self.dir / name).write_text(content, encoding="utf-8")

    def _read(self, name: str) -> Optional[str]:
        path = self.dir / name
        return path.read_text(encoding="utf-8") if path.exists() else None