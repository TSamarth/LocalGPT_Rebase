"""
Runner factory (E3.S2, T1) — builds a ``Runner`` backed by ``DatabaseSessionService``.

Separates runner construction from ``adk_app.py`` so the persistent session
service can be swapped for tests (inject a temp-DB ``DatabaseSessionService`` or
``InMemorySessionService``) and the T2 exporter plugin can be registered once
on the production path via ``plugins=``.

Usage (production)::

    from app.runner import build_runner
    from app.adk_app import app

    runner = build_runner(app)          # → DatabaseSessionService from config
    # or with plugins (T2):
    runner = build_runner(app, plugins=[SessionExporterPlugin(...)])

Usage (tests)::

    from google.adk.sessions import DatabaseSessionService

    svc = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{tmp_path}/test.db")
    runner = build_runner(app, session_service=svc)
"""
from __future__ import annotations

from typing import Optional

from google.adk.apps import App
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.base_session_service import BaseSessionService

from .config import config


def build_runner(
    app: App,
    session_service: Optional[BaseSessionService] = None,
    plugins: Optional[list[BasePlugin]] = None,
) -> Runner:
    """Build a ``Runner`` over *app*, defaulting to a SQLite ``DatabaseSessionService``.

    Args:
        app: The ``App`` instance to run (e.g. ``adk_app.app``).
        session_service: Optional session service override.  When ``None``, a
            ``DatabaseSessionService`` is constructed from ``config.SESSION_DB_URL``.
            Pass an ``InMemorySessionService`` or a temp-DB service for tests.
        plugins: Optional list of plugins to register (e.g. the T2 exporter).
            ADK 2.3.0 requires plugins to live on the ``App``, not the ``Runner``
            directly — so when non-empty a copy of *app* is created with these
            plugins appended to any already registered on the app.  Caller
            plugins whose ``.name`` already exists on the app are skipped, so an
            exporter carried on the App (E4.T0) is not double-registered.

    Returns:
        A ``Runner`` ready to accept ``run_async`` calls.
    """
    if session_service is None:
        session_service = DatabaseSessionService(db_url=config.SESSION_DB_URL)

    # Merge caller-supplied plugins into a fresh App copy when needed.
    # (Runner raises ValueError if ``plugins=`` is passed alongside ``app=``.)
    effective_app = app
    if plugins:
        merged = list(app.plugins or [])
        existing = {p.name for p in merged}
        merged.extend(p for p in plugins if p.name not in existing)
        effective_app = app.model_copy(update={"plugins": merged})

    return Runner(app=effective_app, session_service=session_service)
