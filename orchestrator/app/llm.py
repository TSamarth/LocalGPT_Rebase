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

from typing import Optional, Sequence

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from .config import config


def ollama_model_str(name: str) -> str:
    """LiteLLM provider-qualified model string for a local Ollama chat model."""
    return f"ollama_chat/{name}"


def build_model(model_name: Optional[str] = None) -> LiteLlm:
    """Build the shared Ollama-backed LiteLlm model.

    ``num_ctx`` bounds the KV cache so VRAM stays inside the 14 GB envelope (G2);
    ``keep_alive`` keeps the model resident to avoid reload thrash. ``drop_params``
    lets LiteLLM silently drop any param a provider doesn't accept, so the same
    factory stays robust across model swaps.
    """
    return LiteLlm(
        model=ollama_model_str(model_name or config.REASONING_MODEL),
        api_base=config.OLLAMA_BASE_URL,
        num_ctx=config.MODEL_CONTEXT_TOKENS,
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
    )
