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

from .session_exporter import SessionExporterPlugin
from .workflow import build_research_workflow

app = App(
    name="localgpt_research",
    root_agent=build_research_workflow(),
    resumability_config=ResumabilityConfig(is_resumable=True),
    # Exporter rides on the App so the server-built runner (which bypasses
    # ``build_runner``'s ``plugins=``) still projects artifacts to disk.
    # ``SessionExporterPlugin.__init__`` only builds a ``Path`` — import stays
    # offline-safe and never reaches Ollama.
    plugins=[SessionExporterPlugin()],
)
