import io
import logging
import os
import sys

from app.server import mcp


def _guard_stdio() -> None:
    """Keep stdout clean for the JSON-RPC line protocol (E0.S1).

    Under the MCP stdio transport, stdout is reserved exclusively for JSON-RPC
    messages — any banner/log byte on it (e.g. crawl4ai's ``→ Crawl4AI 0.8.6``
    INIT banner, stray ``print``, or a logging StreamHandler) corrupts a frame.

    We dup the real stdout to a fresh fd and rebind ``sys.stdout`` to it, so the
    MCP framework (which captures ``sys.stdout.buffer`` at ``mcp.run`` time) still
    owns the clean channel. Then we point fd 1 itself at stderr, so any naive
    write to "stdout" — Python-level or from a child/C library — lands on stderr.
    Finally we route the root logger to stderr too.
    """
    saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = io.TextIOWrapper(
        os.fdopen(saved_stdout_fd, "wb"), encoding="utf-8", line_buffering=True
    )
    logging.basicConfig(stream=sys.stderr)


def main():
    _guard_stdio()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
