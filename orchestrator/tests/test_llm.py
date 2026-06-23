"""
Model factory (Story 3.1) — Tier-0 env wiring (E1.T2).

LiteLLM routes some Ollama calls (token counting, embeddings) through the
``OLLAMA_API_BASE`` env var rather than the per-request ``api_base=``. ``build_model``
must mirror the configured base URL into that env var so those paths reach the same
local server. Construction is offline (``LiteLlm`` imports ``litellm`` lazily), so
this runs without Ollama.
"""
from __future__ import annotations

import os

from app.config import config
from app.llm import build_model


def test_build_model_sets_ollama_api_base_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    build_model()
    assert os.environ["OLLAMA_API_BASE"] == config.OLLAMA_BASE_URL
