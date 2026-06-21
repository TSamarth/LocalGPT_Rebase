"""Test fixtures. Sessions are redirected to a tmp dir so tests never touch
the real data/ directory."""
from __future__ import annotations

import pytest

from app.config import config


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Point session/report storage at a throwaway tmp dir for every test."""
    monkeypatch.setattr(config, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(config, "REPORTS_DIR", str(tmp_path / "reports"))
    yield