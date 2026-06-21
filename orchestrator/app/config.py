"""
Configuration for the A2A deep-research orchestrator.

Loaded from environment / .env. Mirrors the crawl4ai MCP config style
(dataclass + _env helpers) for consistency across the two packages.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


@dataclass
class Config:
    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "a2a-deep-research"
    APP_VERSION: str = "0.1.0"

    # ── Ollama / models (architecture.md §1) ───────────────────────────────────
    OLLAMA_BASE_URL: str = field(
        default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    # One hot reasoning model, role-prompted per agent. keep_alive=-1 → stay resident.
    REASONING_MODEL: str = field(
        default_factory=lambda: _env("REASONING_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    )
    # Lighter fallback if 14B + context won't fit the VRAM envelope.
    REASONING_MODEL_FALLBACK: str = field(
        default_factory=lambda: _env("REASONING_MODEL_FALLBACK", "llama3.1:8b-instruct-q5_K_M")
    )
    # Embedding model — must match the one the crawl4ai MCP uses for ChromaDB.
    EMBED_MODEL: str = field(
        default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text")
    )
    # Context window cap (tokens) — bounds KV cache so VRAM stays < 14 GB (G2).
    MODEL_CONTEXT_TOKENS: int = field(
        default_factory=lambda: _env_int("MODEL_CONTEXT_TOKENS", 8192)
    )
    # Ollama keep_alive seconds; -1 keeps the model resident (no reload thrash).
    MODEL_KEEP_ALIVE: int = field(
        default_factory=lambda: _env_int("MODEL_KEEP_ALIVE", -1)
    )

    # ── crawl4ai MCP server (stdio subprocess) ─────────────────────────────────
    # Path to the MCP package launched via ADK MCPToolset.
    MCP_SERVER_CWD: str = field(
        default_factory=lambda: _env("MCP_SERVER_CWD", "../mcp/Crawl4AI_MCP")
    )

    # ── Storage / artifacts (architecture.md §5) ───────────────────────────────
    SESSIONS_DIR: str = field(
        default_factory=lambda: _env("SESSIONS_DIR", "./data/sessions")
    )
    REPORTS_DIR: str = field(
        default_factory=lambda: _env("REPORTS_DIR", "./data/reports")
    )

    # ── Research loop / stop-rule (architecture.md §4) ─────────────────────────
    # Hard cap on Acquire→Extract→Verify passes per subtopic (runaway guard).
    MAX_ITERATIONS_PER_SUBTOPIC: int = field(
        default_factory=lambda: _env_int("MAX_ITERATIONS_PER_SUBTOPIC", 4)
    )
    # Diminishing-returns floor: stop a subtopic if a pass adds fewer than this
    # many new unique claims.
    MIN_NEW_CLAIMS: int = field(
        default_factory=lambda: _env_int("MIN_NEW_CLAIMS", 2)
    )

    # ── Verification (architecture.md §3.6, Open Q2) ───────────────────────────
    # Two sources count as independent iff different eTLD+1 AND content cosine
    # below this threshold (catches syndication/mirrors). Starting value — tune
    # against a labelled mirror set (workflow risk note).
    INDEPENDENCE_COSINE_THRESHOLD: float = field(
        default_factory=lambda: _env_float("INDEPENDENCE_COSINE_THRESHOLD", 0.92)
    )
    # Temporal drift (FR5.6, §12.2): publication_date gap (months) at/above which a
    # contradiction is classified temporal_drift instead of factual. Default 18 months.
    TEMPORAL_DRIFT_THRESHOLD_MONTHS: int = field(
        default_factory=lambda: _env_int("TEMPORAL_DRIFT_THRESHOLD_MONTHS", 18)
    )

    # ── Adaptive depth → target_evidence per subtopic ──────────────────────────
    TARGET_EVIDENCE_SHALLOW: int = field(
        default_factory=lambda: _env_int("TARGET_EVIDENCE_SHALLOW", 2)
    )
    TARGET_EVIDENCE_NORMAL: int = field(
        default_factory=lambda: _env_int("TARGET_EVIDENCE_NORMAL", 3)
    )
    TARGET_EVIDENCE_DEEP: int = field(
        default_factory=lambda: _env_int("TARGET_EVIDENCE_DEEP", 5)
    )

    def ensure_data_dirs(self) -> None:
        """Create session/report directories if they don't exist."""
        Path(self.SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.REPORTS_DIR).mkdir(parents=True, exist_ok=True)


# Singleton instance
config = Config()