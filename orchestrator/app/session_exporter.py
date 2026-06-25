"""
SessionExporterPlugin (E3.S2, T2) — projects ADK session state → disk.

Pure export: reads from ADK events/session state, writes to disk only.
Never feeds control flow and never writes back to the ADK session.

Files written (byte-identical to v1 SessionStore output per architecture §176):
    {sessions_dir}/{session_id}/plan.json          — ResearchPlan.model_dump_json(indent=2)
    {sessions_dir}/{session_id}/claim_ledger.json  — ClaimLedger.model_dump_json(indent=2)
    {sessions_dir}/{session_id}/draft.md           — raw markdown string

No stage.json (retired as v2 truth for E3.S2+).

ADK API anchors (verified against installed ADK 2.3.0):
  - BasePlugin.__init__(name: str)
  - on_event_callback(*, invocation_context, event) -> Optional[Event]
    return None to leave the event unmodified.
  - after_run_callback(*, invocation_context) -> None
  - event.actions: EventActions (always set, default_factory=EventActions)
  - event.actions.state_delta: dict[str, Any] (always set, default_factory=dict)
  - invocation_context.session.id: str  — unique session identifier
  - invocation_context.session.state: dict[str, Any]  — accumulated session state
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin

from .config import config
from .schemas import ClaimLedger, ResearchPlan

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext


class SessionExporterPlugin(BasePlugin):
    """Projects ADK session state → disk.  Pure export, never feeds control flow."""

    PLAN_FILE = "plan.json"
    LEDGER_FILE = "claim_ledger.json"
    DRAFT_FILE = "draft.md"

    def __init__(self, sessions_dir: str = config.SESSIONS_DIR) -> None:
        super().__init__(name="session_exporter")
        self.sessions_dir = Path(sessions_dir)

    # ── incremental write (per-event) ─────────────────────────────────────────

    async def on_event_callback(
        self,
        *,
        invocation_context: InvocationContext,
        event: Event,
    ) -> Optional[Event]:
        """Incremental write when a state_delta contains v2_* keys.

        Returns ``None`` to leave the event unmodified (pure export side-effect).
        ``event.actions.state_delta`` is always a ``dict`` (default ``{}``) so
        the truthiness check below is safe.
        """
        delta = event.actions.state_delta
        if not delta:
            return None

        session_dir = self._session_dir(invocation_context)

        if "v2_plan" in delta:
            plan = ResearchPlan.model_validate(delta["v2_plan"])
            (session_dir / self.PLAN_FILE).write_text(
                plan.model_dump_json(indent=2), encoding="utf-8"
            )

        if "v2_ledger" in delta:
            ledger = ClaimLedger.model_validate(delta["v2_ledger"])
            (session_dir / self.LEDGER_FILE).write_text(
                ledger.model_dump_json(indent=2), encoding="utf-8"
            )

        if "v2_draft" in delta:
            (session_dir / self.DRAFT_FILE).write_text(
                delta["v2_draft"], encoding="utf-8"
            )

        return None

    # ── final flush (post-run) ────────────────────────────────────────────────

    async def after_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> None:
        """Final flush from authoritative session state.

        Re-reads ``invocation_context.session.state`` and overwrites the disk
        files so all three artifacts remain consistent even if incremental events
        were missed (e.g. an in-process resume skipped some event delivery paths).
        Safe to call multiple times — idempotent.
        """
        state = invocation_context.session.state
        if not any(k in state for k in ("v2_plan", "v2_ledger", "v2_draft")):
            return

        session_dir = self._session_dir(invocation_context)

        for key, filename, cls in [
            ("v2_plan", self.PLAN_FILE, ResearchPlan),
            ("v2_ledger", self.LEDGER_FILE, ClaimLedger),
        ]:
            if key in state:
                obj = cls.model_validate(state[key])
                (session_dir / filename).write_text(
                    obj.model_dump_json(indent=2), encoding="utf-8"
                )

        if "v2_draft" in state:
            (session_dir / self.DRAFT_FILE).write_text(
                state["v2_draft"], encoding="utf-8"
            )

    # ── private helpers ───────────────────────────────────────────────────────

    def _session_dir(self, invocation_context: InvocationContext) -> Path:
        """Return the per-session output directory, creating it if absent."""
        session_dir = self.sessions_dir / invocation_context.session.id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir
