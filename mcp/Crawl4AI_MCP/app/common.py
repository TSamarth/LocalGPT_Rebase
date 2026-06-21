"""
Central registration of all MCP tools, resources, and prompts.
Follows the DRY common.py pattern — both server entry points import this.
"""
from __future__ import annotations

import functools
import inspect
from typing import Awaitable, Callable

from fastmcp import FastMCP

from app.prompts.research import deep_research_plan
from app.resources.static import get_capabilities, get_status
from app.tools.adaptive_crawl import adaptive_crawl
from app.tools.crawl import crawl_many, crawl_url
from app.tools.deep_crawl import deep_crawl
from app.tools.discover import discover_urls
from app.tools.search import get_crawl_stats, search_chunks
from app.tools.triage import score_and_triage_urls
from app.utils import ToolResponse


def _register_tool(mcp: FastMCP, fn: Callable[..., Awaitable]) -> None:
    """Register a tool wrapped in the standard ToolResponse envelope.

    Tool functions return plain dicts; this wrapper turns every result into
    ``{ok, error, data}`` and converts unhandled exceptions into a uniform
    ``ok=False`` failure instead of an opaque MCP error — so ADK can branch
    on ``ok`` reliably. The wrapped signature is preserved (params + ctx) so
    FastMCP still generates the correct input schema.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs) -> ToolResponse:
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:  # surface failures through the envelope, not a raw throw
            return ToolResponse(ok=False, error=f"{type(e).__name__}: {e}")
        data = result if isinstance(result, dict) else {"result": result}
        return ToolResponse(ok=True, data=data)

    # Preserve the original parameters but advertise ToolResponse as the output schema.
    wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=ToolResponse)
    wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "return": ToolResponse}
    mcp.tool()(wrapper)


def register_all(mcp: FastMCP) -> None:
    """Register all tools, resources, and prompts with the FastMCP server."""

    # ── Tools ────────────────────────────────────────────────────────────────
    _register_tool(mcp, discover_urls)
    _register_tool(mcp, score_and_triage_urls)
    _register_tool(mcp, crawl_url)
    _register_tool(mcp, crawl_many)
    _register_tool(mcp, deep_crawl)
    _register_tool(mcp, adaptive_crawl)
    _register_tool(mcp, search_chunks)
    _register_tool(mcp, get_crawl_stats)

    # ── Resources ────────────────────────────────────────────────────────────
    mcp.resource("crawl4ai://status")(get_status)
    mcp.resource("crawl4ai://capabilities")(get_capabilities)

    # ── Prompts ──────────────────────────────────────────────────────────────
    mcp.prompt()(deep_research_plan)

