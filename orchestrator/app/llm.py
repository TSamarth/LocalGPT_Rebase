"""
Shared one-hot model factory — the foundation every agent is built on (Story 3.1).

All six specialists are the SAME resident model (``config.REASONING_MODEL``,
Qwen2.5-14B Q4) reached through ADK's ``LiteLlm`` → local Ollama, differentiated
only by a role system prompt. Centralising construction here keeps the model
string, ``api_base`` and generation knobs in one place so the agents can never
drift apart (architecture.md §1 — single hot model, role-prompted per agent).

Construction is offline and cheap: ``LiteLlm`` imports ``litellm`` lazily on the
first generate call, so unit tests can build agents and mock the model without a
running Ollama.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from .config import config

logger = logging.getLogger("orchestrator.llm")

# litellm callbacks are PROCESS-GLOBAL, so registration must happen at most once
# per process regardless of how many models are built.
_raw_llm_logger_registered = False

#: Deterministic generation for schema/tool agents (Clarifier/Planner/Acquirer/
#: Extractor/Verifier): the Ollama default temperature (0.8) makes a Q4 14B model
#: drift off its role prompt (prose instead of tool calls / JSON). Applied via
#: ``LlmAgent.generate_content_config`` — NEVER via ``LiteLlm(...)`` kwargs, which
#: land in ``_additional_args`` and are applied last, silently overriding any
#: per-agent config.
DETERMINISTIC_CONFIG = types.GenerateContentConfig(temperature=0.0)

#: Slightly warmer generation for the Writer: it composes prose from an already
#: pinned ClaimLedger, so a little sampling improves readability without risking
#: the schema/tool drift the other five agents must avoid. Same application rule
#: as ``DETERMINISTIC_CONFIG`` — via ``generate_content_config`` only.
WRITER_CONFIG = types.GenerateContentConfig(temperature=0.3)


def ollama_model_str(name: str) -> str:
    """LiteLLM provider-qualified model string for a local Ollama chat model."""
    return f"ollama_chat/{name}"


def _register_raw_llm_logger() -> None:
    """Register a litellm ``CustomLogger`` that dumps the raw litellm↔Ollama wire
    request/response (incl. Ollama ``eval_count``/``total_duration``) to
    ``{SESSIONS_DIR}/llm_raw.jsonl``.

    Opt-in (``TRACE_LLM_RAW``, default False) and idempotent: litellm callbacks
    are process-global, so a module-level flag guards double registration. P2's
    ``TracePlugin`` already covers tokens/latency at the ADK level; this adds the
    raw provider payloads only. The file is process-wide (not per-session) — the
    ADK session id is not visible at the litellm callback layer.
    """
    global _raw_llm_logger_registered
    if _raw_llm_logger_registered:
        return

    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    out_path = Path(config.SESSIONS_DIR) / "llm_raw.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _serialize(obj: Any) -> Any:
        for attr in ("model_dump", "dict"):
            fn = getattr(obj, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:  # noqa: BLE001 — best-effort serialization
                    pass
        return repr(obj)

    class _RawLLMLogger(CustomLogger):
        def _emit(self, event: str, kwargs: dict, response_obj: Any,
                  start_time: Any, end_time: Any) -> None:
            try:
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": event,
                    "model": kwargs.get("model"),
                    "messages": kwargs.get("messages"),
                    # Ollama's raw /api/chat body (carries eval_count/total_duration).
                    "original_response": kwargs.get("original_response"),
                    "response": _serialize(response_obj) if response_obj is not None else None,
                    "hidden_params": _serialize(getattr(response_obj, "_hidden_params", None))
                    if response_obj is not None else None,
                }
                with out_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=repr, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001 — a logging bug must never break a run
                logger.exception("raw llm log write failed")

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._emit("success", kwargs, response_obj, start_time, end_time)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._emit("success", kwargs, response_obj, start_time, end_time)

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._emit("failure", kwargs, response_obj, start_time, end_time)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._emit("failure", kwargs, response_obj, start_time, end_time)

    litellm.callbacks.append(_RawLLMLogger())
    _raw_llm_logger_registered = True
    logger.info("raw litellm↔Ollama logging enabled → %s", out_path)


def build_model(model_name: Optional[str] = None) -> LiteLlm:
    """Build the shared Ollama-backed LiteLlm model.

    ``num_ctx`` bounds the KV cache so VRAM stays inside the 14 GB envelope (G2);
    ``keep_alive`` keeps the model resident to avoid reload thrash. ``drop_params``
    lets LiteLLM silently drop any param a provider doesn't accept, so the same
    factory stays robust across model swaps.
    """
    # LiteLLM routes some Ollama calls (token counting, embeddings) through the
    # OLLAMA_API_BASE env var rather than the per-request ``api_base=``; mirror the
    # configured base URL into it so those paths reach the same local server
    # (architecture.md §15 Tier-0).
    os.environ["OLLAMA_API_BASE"] = config.OLLAMA_BASE_URL
    if config.TRACE_LLM_RAW:
        _register_raw_llm_logger()
    return LiteLlm(
        model=ollama_model_str(model_name or config.REASONING_MODEL),
        api_base=config.OLLAMA_BASE_URL,
        num_ctx=config.MODEL_CONTEXT_TOKENS,
        num_predict=config.MODEL_MAX_OUTPUT_TOKENS,
        keep_alive=config.MODEL_KEEP_ALIVE,
        drop_params=True,
    )


def build_agent(
    *,
    name: str,
    role_prompt: str,
    output_schema: Optional[type] = None,
    output_key: Optional[str] = None,
    tools: Optional[Sequence[object]] = None,
    model: Optional[LiteLlm] = None,
    generate_content_config: Optional[types.GenerateContentConfig] = None,
) -> LlmAgent:
    """Construct a role-prompted agent on the shared model.

    Reasoning-only agents (Clarifier, Planner, Writer) pass an ``output_schema``
    for structured JSON and no tools. Tool-holding agents (Acquirer, Extractor,
    Verifier) pass ``tools`` and leave ``output_schema`` unset — ADK disallows
    combining a forced output schema with tool/transfer use.
    """
    if output_schema is not None and tools:
        raise ValueError(
            f"agent {name!r}: output_schema and tools are mutually exclusive in ADK"
        )
    return LlmAgent(
        name=name,
        model=model or build_model(),
        instruction=role_prompt,
        output_schema=output_schema,
        output_key=output_key,
        tools=list(tools) if tools else [],
        generate_content_config=generate_content_config,
    )
