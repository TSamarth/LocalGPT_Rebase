"""
Session/export retention pruning (M4).

Nothing in the orchestrator evicts old data: the ADK ``DatabaseSessionService``
rows and the per-session export dirs under ``{SESSIONS_DIR}/{session_id}/`` grow
monotonically. This module adds an **explicit, opt-in** maintenance command that
deletes sessions + exports older than a retention window.

It is deliberately NOT wired into server/main startup: a startup sweep risks
deleting a session mid-resume. Retention is disabled by default
(``SESSION_RETENTION_DAYS=0``), so nothing is ever deleted without an explicit
``--retention-days N`` invocation of this command.

Run it with::

    python -m app.maintenance --retention-days 30 [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.adk.sessions import BaseSessionService, DatabaseSessionService

from app.config import config
from app.server import APP_NAME

logger = logging.getLogger("orchestrator.maintenance")


@dataclass
class PruneResult:
    """What a prune run removed (or, in ``dry_run``, would remove)."""

    db_session_ids: list[str] = field(default_factory=list)
    export_dirs: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def db_count(self) -> int:
        return len(self.db_session_ids)

    @property
    def export_count(self) -> int:
        return len(self.export_dirs)


async def prune_sessions(
    *,
    retention_days: int,
    session_service: BaseSessionService,
    sessions_dir: Path,
    app_name: str = APP_NAME,
    now: datetime | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """Delete DB sessions + export dirs whose last update is older than the window.

    ``retention_days <= 0`` disables pruning (no-op, zero counts). A session is
    stale when its ADK ``Session.last_update_time`` (unix epoch seconds) — or an
    export dir's mtime — predates ``now - retention_days``. DB rows and export
    dirs are pruned independently; a single failing delete is logged and skipped
    so one bad path never aborts the sweep. With ``dry_run`` the result lists the
    targets without deleting anything.
    """
    result = PruneResult(dry_run=dry_run)
    if retention_days <= 0:
        return result

    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).timestamp()

    # ── DB sessions ────────────────────────────────────────────────────────────
    # list_sessions returns summaries (no events) — id + last_update_time is all
    # we need. user_id=None enumerates every user's sessions for the app.
    resp = await session_service.list_sessions(app_name=app_name)
    for session in resp.sessions:
        if session.last_update_time >= cutoff:
            continue
        if dry_run:
            result.db_session_ids.append(session.id)
            continue
        try:
            await session_service.delete_session(
                app_name=app_name,
                user_id=session.user_id,
                session_id=session.id,
            )
        except Exception:
            logger.exception("failed to delete session %s", session.id)
            continue
        result.db_session_ids.append(session.id)

    # ── Export dirs ────────────────────────────────────────────────────────────
    # Only immediate subdirectories are per-session exports; stray top-level files
    # (e.g. llm_raw.jsonl) are left untouched.
    sessions_dir = Path(sessions_dir)
    if sessions_dir.is_dir():
        for child in sessions_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                logger.exception("failed to stat export dir %s", child)
                continue
            if dry_run:
                result.export_dirs.append(child.name)
                continue
            try:
                shutil.rmtree(child)
            except OSError:
                logger.exception("failed to remove export dir %s", child)
                continue
            result.export_dirs.append(child.name)

    return result


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.maintenance",
        description="Prune sessions + exports older than the retention window (M4).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=config.SESSION_RETENTION_DAYS,
        help="Prune data older than N days (default: SESSION_RETENTION_DAYS; 0 disables).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be pruned without deleting anything.",
    )
    args = parser.parse_args(argv)

    session_service = DatabaseSessionService(db_url=config.SESSION_DB_URL)
    result = await prune_sessions(
        retention_days=args.retention_days,
        session_service=session_service,
        sessions_dir=Path(config.SESSIONS_DIR),
        dry_run=args.dry_run,
    )
    verb = "would prune" if result.dry_run else "pruned"
    print(f"{verb} {result.db_count} DB session(s), {result.export_count} export dir(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
