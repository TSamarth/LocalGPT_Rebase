"""
Shared constructor for the crawl4ai ``McpToolset`` (stdio subprocess).

The Acquirer, Extractor and Verifier each need a crawl4ai MCP toolset, differing
ONLY in their ``tool_filter`` (least-privilege, architecture.md §1). This module
holds the single construction body they all delegate to (kills the triplicated
``_build_default_toolset`` copies — finding R1) and centralises the connection
timeout knob (finding M1).

``mcp`` is an optional ADK extra, so the ADK imports stay INSIDE the function:
the orchestrator (and the offline unit tests) can load the agent modules — and
inject a fake toolset — without ``mcp`` installed.
"""
from __future__ import annotations

from pathlib import Path

from google.adk.tools.base_toolset import BaseToolset

from ..config import config


def build_crawl4ai_toolset(tool_filter: list[str]) -> BaseToolset:
    """Construct the real crawl4ai ``McpToolset`` (stdio subprocess).

    Args:
        tool_filter: The exact MCP tool names this toolset exposes. Each agent
            passes only the tools it is permitted to hold, so a shared subprocess
            (see ``workflow.research``) still yields per-agent least-privilege
            views.

    The server is launched exactly as its own package documents (venv python over
    stdio ``main.py``) from ``config.MCP_SERVER_CWD``. ``config.MCP_TOOL_TIMEOUT_SEC``
    is the single ``StdioConnectionParams.timeout`` knob (M1): ADK uses it for BOTH
    the stdio startup/``initialize`` handshake AND the per-tool-call read timeout.
    """
    from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
    from mcp import StdioServerParameters

    server_cwd_path = Path(config.MCP_SERVER_CWD).resolve()
    server_cwd = str(server_cwd_path)
    # Use the venv python directly to avoid `uv run` startup overhead (~2-4 s on
    # Windows), which caused the old default 5 s timeout to fire before the MCP
    # server finished initializing inside the event loop.
    venv_python = str(server_cwd_path / ".venv" / "Scripts" / "python.exe")
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=venv_python,
                args=["main.py"],
                cwd=server_cwd,
            ),
            timeout=config.MCP_TOOL_TIMEOUT_SEC,
        ),
        tool_filter=list(tool_filter),
    )
