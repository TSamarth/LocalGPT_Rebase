"""Agents-dir discovery shim (E4.T0) — re-exports the ADK ``App`` as ``app``.

``get_fast_api_app(agents_dir=...)`` / ``adk api_server`` discover an app by
importing a module whose name equals the app_name and reading its ``app``
symbol.  With ``agents_dir`` set to ``orchestrator/``, ``import localgpt_research``
resolves here and exposes the same ``App`` instance built in ``app/adk_app.py``
(no separate App — single source of truth).
"""
from __future__ import annotations

from app.adk_app import app

__all__ = ["app"]
