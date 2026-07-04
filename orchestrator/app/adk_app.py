"""
ADK ``App`` wiring (E2.S1, T2) — the resumable container for the research workflow.

Resumability in ADK 2.x lives on the ``App``, not the ``Workflow``: an
``App(resumability_config=ResumabilityConfig(is_resumable=True))`` makes the
dynamic workflow auto-checkpoint each ``run_node`` so a killed run can resume by
``invocation_id`` and skip already-completed sub-nodes. This is the foundation the
T5 resume gate depends on (it needs T2 landed, not just the CP1 RequestInput of T4).

The module-level ``app`` follows the ADK CLI convention (the CLI looks up a symbol
named ``app``). Building the default workflow constructs the clarifier/planner
``LlmAgent``s over ``LiteLlm``, but ``LiteLlm`` imports ``litellm`` lazily on first
generate — so importing this module is offline-safe and never reaches Ollama.
"""
from __future__ import annotations

from google.adk.apps import App, ResumabilityConfig

from .config import config
from .session_exporter import SessionExporterPlugin
from .trace_plugin import TracePlugin
from .workflow import build_research_workflow

# Pure observers, ride on the App so the server-built runner (which bypasses
# ``build_runner``'s ``plugins=``) still gets them. Both __init__s only build
# ``Path``s — import stays offline-safe and never reaches Ollama. TracePlugin is
# gated on config.TRACE_ENABLED (default True).
_plugins = [SessionExporterPlugin()]
if config.TRACE_ENABLED:
    _plugins.append(TracePlugin())

app = App(
    name="localgpt_research",
    root_agent=build_research_workflow(),
    resumability_config=ResumabilityConfig(is_resumable=True),
    plugins=_plugins,
)
