"""M4 retention pruning — DB sessions + export dirs older than the window.

Fully offline: a temp-file ``DatabaseSessionService`` and a temp ``sessions_dir``
of fake per-session export dirs. asyncio_mode="auto" runs the async tests without
per-function markers (see pyproject).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from google.adk.sessions import DatabaseSessionService
from sqlalchemy import text

from app.maintenance import prune_sessions

APP_NAME = "localgpt_research"
USER_ID = "u1"


async def _make_service(tmp_path):
    return DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{tmp_path}/test.db")


async def _backdate(service, session_id: str, when: datetime) -> None:
    """Force a session's stored update_time so it predates the cutoff."""
    async with service.db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET update_time = :t WHERE id = :sid"),
            {"t": when.replace(tzinfo=None), "sid": session_id},
        )


async def test_prunes_only_stale_db_session(tmp_path):
    service = await _make_service(tmp_path)
    stale = await service.create_session(app_name=APP_NAME, user_id=USER_ID)
    fresh = await service.create_session(app_name=APP_NAME, user_id=USER_ID)
    await _backdate(service, stale.id, datetime.now(timezone.utc) - timedelta(days=40))

    result = await prune_sessions(
        retention_days=30,
        session_service=service,
        sessions_dir=tmp_path / "sessions",
        app_name=APP_NAME,
    )

    assert result.db_session_ids == [stale.id]
    remaining = await service.list_sessions(app_name=APP_NAME)
    assert {s.id for s in remaining.sessions} == {fresh.id}


async def test_prunes_only_old_export_dir(tmp_path):
    service = await _make_service(tmp_path)
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    old = sessions_dir / "old-session"
    fresh = sessions_dir / "fresh-session"
    old.mkdir()
    fresh.mkdir()
    (old / "plan.json").write_text("{}", encoding="utf-8")
    stray = sessions_dir / "llm_raw.jsonl"
    stray.write_text("x", encoding="utf-8")

    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(old, (old_ts, old_ts))

    result = await prune_sessions(
        retention_days=30,
        session_service=service,
        sessions_dir=sessions_dir,
        app_name=APP_NAME,
    )

    assert result.export_dirs == ["old-session"]
    assert not old.exists()
    assert fresh.exists()
    assert stray.exists()  # stray top-level file untouched


async def test_retention_zero_is_noop(tmp_path):
    service = await _make_service(tmp_path)
    stale = await service.create_session(app_name=APP_NAME, user_id=USER_ID)
    await _backdate(service, stale.id, datetime.now(timezone.utc) - timedelta(days=40))
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    old = sessions_dir / "old-session"
    old.mkdir()
    os.utime(old, (0, 0))

    result = await prune_sessions(
        retention_days=0,
        session_service=service,
        sessions_dir=sessions_dir,
        app_name=APP_NAME,
    )

    assert result.db_count == 0
    assert result.export_count == 0
    assert old.exists()
    remaining = await service.list_sessions(app_name=APP_NAME)
    assert len(remaining.sessions) == 1


async def test_dry_run_reports_but_deletes_nothing(tmp_path):
    service = await _make_service(tmp_path)
    stale = await service.create_session(app_name=APP_NAME, user_id=USER_ID)
    await _backdate(service, stale.id, datetime.now(timezone.utc) - timedelta(days=40))
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    old = sessions_dir / "old-session"
    old.mkdir()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(old, (old_ts, old_ts))

    result = await prune_sessions(
        retention_days=30,
        session_service=service,
        sessions_dir=sessions_dir,
        app_name=APP_NAME,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.db_session_ids == [stale.id]
    assert result.export_dirs == ["old-session"]
    # Nothing actually deleted.
    assert old.exists()
    remaining = await service.list_sessions(app_name=APP_NAME)
    assert len(remaining.sessions) == 1
