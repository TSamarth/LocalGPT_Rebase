"""
Shared utilities for the Crawl4AI MCP server.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from crawl4ai import CacheMode
from pydantic import BaseModel, Field


class ToolResponse(BaseModel):
    """Standard result envelope for every MCP tool.

    Gives ADK (and any MCP client) a uniform, typed shape to branch on:
    check ``ok`` first, then read ``data`` on success or ``error`` on failure.
    """

    ok: bool
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


def make_id() -> str:
    return str(uuid.uuid4())


def get_cache_mode(mode: str) -> CacheMode:
    mapping = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    return mapping.get(mode.lower(), CacheMode.ENABLED)
