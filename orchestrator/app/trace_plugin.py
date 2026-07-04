"""
TracePlugin — per-session JSONL trace of agent / model / tool activity.

Pure observer: every callback logs one JSON line to
``{sessions_dir}/{session_id}/trace.jsonl`` and returns ``None`` so it never
short-circuits or modifies execution. Callback bodies are individually guarded —
a tracing bug must never break a research run.

Record shape (one JSON object per line):
    {"ts": <iso8601 utc>, "type": <record type>, "invocation_id": ..., ...}

Record types:
    agent_start / agent_end       — agent name
    model_request                 — agent, last user content (truncated), tools offered
    model_response                — text/function_calls (truncated), token usage, latency_ms
    model_error                   — error type + message
    tool_start                    — tool name + args (truncated)
    tool_end                      — tool name, result (truncated), latency_ms
    tool_error                    — tool name, error type + message
    event                         — author, is_final, state_delta keys

ADK API anchors (verified against installed ADK 2.3.0
``google/adk/plugins/base_plugin.py``): all callbacks are keyword-only async;
returning ``None`` always lets execution proceed unmodified.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin

from .config import config

if TYPE_CHECKING:
    from google.adk.agents.base_agent import BaseAgent
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger("orchestrator.trace")


def _truncate(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[truncated {len(text) - limit} chars]"


class TracePlugin(BasePlugin):
    """Writes one JSONL trace line per agent/model/tool callback. Pure observer."""

    TRACE_FILE = "trace.jsonl"

    def __init__(
        self,
        sessions_dir: str = config.SESSIONS_DIR,
        truncate_chars: int = config.TRACE_TRUNCATE_CHARS,
    ) -> None:
        super().__init__(name="trace")
        self.sessions_dir = Path(sessions_dir)
        self.truncate_chars = truncate_chars
        # Start times for latency pairing, keyed by (invocation_id, kind, name).
        self._starts: dict[tuple, float] = {}
        # One append-mode file handle per session, opened lazily on first write
        # (mkdir happens once, at open time). Kept open and flushed per write so
        # traces are durable; closed on after_run/close teardown.
        self._handles: dict[str, Any] = {}

    # ── record plumbing ────────────────────────────────────────────────────────

    def _handle_for(self, session_id: str):
        fh = self._handles.get(session_id)
        if fh is None:
            session_dir = self.sessions_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            fh = (session_dir / self.TRACE_FILE).open("a", encoding="utf-8")
            self._handles[session_id] = fh
        return fh

    def _write(self, session_id: str, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
        fh = self._handle_for(session_id)
        fh.write(json.dumps(record, ensure_ascii=False, default=repr) + "\n")
        fh.flush()

    def _close_handle(self, session_id: str) -> None:
        fh = self._handles.pop(session_id, None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                logger.exception("trace handle close failed for session %r", session_id)

    def _emit(self, ctx: Any, record: dict[str, Any]) -> None:
        """Resolve session/invocation ids from a callback/invocation context and
        write the record; swallow (but log) every tracing failure."""
        try:
            invocation_id = getattr(ctx, "invocation_id", None)
            session_id = None
            # InvocationContext has .session; CallbackContext exposes ._invocation_context.
            session = getattr(ctx, "session", None)
            if session is None:
                inner = getattr(ctx, "_invocation_context", None)
                session = getattr(inner, "session", None)
            if session is not None:
                session_id = session.id
            self._write(
                session_id or "unknown-session",
                {"invocation_id": invocation_id, **record},
            )
        except Exception:
            logger.exception("trace write failed for record type %r", record.get("type"))

    # ── agent callbacks ────────────────────────────────────────────────────────

    async def before_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        self._emit(callback_context, {"type": "agent_start", "agent": agent.name})
        return None

    async def after_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        self._emit(callback_context, {"type": "agent_end", "agent": agent.name})
        return None

    # ── model callbacks ────────────────────────────────────────────────────────

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> None:
        try:
            agent = getattr(callback_context, "agent_name", None)
            key = (getattr(callback_context, "invocation_id", None), "model", agent)
            self._starts[key] = time.monotonic()
            tools = list(getattr(llm_request, "tools_dict", {}) or {})
            contents = getattr(llm_request, "contents", None) or []
            last_text = ""
            if contents:
                parts = getattr(contents[-1], "parts", None) or []
                last_text = " ".join(p.text for p in parts if getattr(p, "text", None))
            self._emit(
                callback_context,
                {
                    "type": "model_request",
                    "agent": agent,
                    "tools_offered": tools,
                    "last_content": _truncate(last_text, self.truncate_chars),
                },
            )
        except Exception:
            logger.exception("trace before_model failed")
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> None:
        try:
            agent = getattr(callback_context, "agent_name", None)
            key = (getattr(callback_context, "invocation_id", None), "model", agent)
            started = self._starts.pop(key, None)
            latency_ms = round((time.monotonic() - started) * 1000) if started else None

            text = ""
            function_calls: list[dict[str, Any]] = []
            content = getattr(llm_response, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None):
                    text += part.text
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    function_calls.append(
                        {"name": fc.name, "args": _truncate(fc.args, self.truncate_chars)}
                    )

            usage = getattr(llm_response, "usage_metadata", None)
            tokens = None
            if usage is not None:
                tokens = {
                    "prompt": getattr(usage, "prompt_token_count", None),
                    "response": getattr(usage, "candidates_token_count", None),
                    "total": getattr(usage, "total_token_count", None),
                }
            self._emit(
                callback_context,
                {
                    "type": "model_response",
                    "agent": agent,
                    "text": _truncate(text, self.truncate_chars),
                    "function_calls": function_calls,
                    "tokens": tokens,
                    "latency_ms": latency_ms,
                },
            )
        except Exception:
            logger.exception("trace after_model failed")
        return None

    async def on_model_error_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
        error: Exception,
    ) -> None:
        # Pop the paired start entry (same key as after_model) so a failed model
        # call does not leak into the process-wide _starts dict.
        agent = getattr(callback_context, "agent_name", None)
        self._starts.pop((getattr(callback_context, "invocation_id", None), "model", agent), None)
        self._emit(
            callback_context,
            {
                "type": "model_error",
                "agent": agent,
                "error_type": type(error).__name__,
                "error": _truncate(str(error), self.truncate_chars),
            },
        )
        return None

    # ── tool callbacks ─────────────────────────────────────────────────────────

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext
    ) -> None:
        try:
            key = (getattr(tool_context, "invocation_id", None), "tool", tool.name)
            self._starts[key] = time.monotonic()
            self._emit(
                tool_context,
                {
                    "type": "tool_start",
                    "tool": tool.name,
                    "args": _truncate(tool_args, self.truncate_chars),
                },
            )
        except Exception:
            logger.exception("trace before_tool failed")
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict,
    ) -> None:
        try:
            key = (getattr(tool_context, "invocation_id", None), "tool", tool.name)
            started = self._starts.pop(key, None)
            latency_ms = round((time.monotonic() - started) * 1000) if started else None
            self._emit(
                tool_context,
                {
                    "type": "tool_end",
                    "tool": tool.name,
                    "result": _truncate(result, self.truncate_chars),
                    "latency_ms": latency_ms,
                },
            )
        except Exception:
            logger.exception("trace after_tool failed")
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> None:
        # Pop the paired start entry (same key as after_tool) so a failed tool
        # call does not leak into the process-wide _starts dict.
        self._starts.pop((getattr(tool_context, "invocation_id", None), "tool", tool.name), None)
        self._emit(
            tool_context,
            {
                "type": "tool_error",
                "tool": tool.name,
                "error_type": type(error).__name__,
                "error": _truncate(str(error), self.truncate_chars),
            },
        )
        return None

    # ── event callback ─────────────────────────────────────────────────────────

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Optional[Event]:
        try:
            delta = event.actions.state_delta if event.actions else {}
            self._write(
                invocation_context.session.id,
                {
                    "type": "event",
                    "invocation_id": invocation_context.invocation_id,
                    "author": event.author,
                    "is_final": event.is_final_response(),
                    "state_delta_keys": sorted(delta or {}),
                },
            )
        except Exception:
            logger.exception("trace on_event failed")
        return None

    # ── teardown ───────────────────────────────────────────────────────────────

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        # Close this session's trace handle at run end to bound open handles on a
        # long-lived server; reopened lazily if the session traces again.
        try:
            self._close_handle(invocation_context.session.id)
        except Exception:
            logger.exception("trace after_run close failed")
        return None

    async def close(self) -> None:
        # Runner shutdown: flush and close any remaining handles.
        for session_id in list(self._handles):
            self._close_handle(session_id)
