"""
Research workflow skeleton (E2.S1, T1+T3 merged) — the v2 ADK dynamic-workflow shell.

This replaces the v1 sync orchestrator driver (``Orchestrator.step`` plus the
``_run_sync`` / ``_drive`` ``InMemoryRunner``-per-handler bridge) with a single
ADK **dynamic Workflow**. The vertical slice here is the smallest one that proves
the engine: ``clarify → plan → CP1 (plan approval)``, running ``ctx.run_node``-native
from the start so there is **no** ``_drive`` Runner anywhere in the slice path
(that is the T3 gate, satisfied by construction).

Why ``ctx.run_node`` instead of the v1 bridge: ADK 2.x's ``Context.run_node``
accepts an ``LlmAgent`` *or* a ``@node`` function as its target and returns the
node's output **directly**. So the existing ``build_clarifier()`` / ``build_planner()``
``LlmAgent``s plug in unchanged — no agent rewrite, no per-call Runner, no manual
session-state plumbing.

DI seam (mirrors the v1 ``PipelineDeps``): :func:`build_research_workflow` takes
optional ``clarifier_node`` / ``planner_node``. Defaults are agent-backed nodes
built from ``build_clarifier(model=build_model())`` / ``build_planner(model=...)``;
tests inject canned ``@node`` stubs so the slice runs fully offline (no Ollama, no
network).

Design choice for the DI seam — **closure**: the ``research`` parent node is
defined *inside* :func:`build_research_workflow` so it closes over the injected
nodes. This keeps ``research`` a valid ``@node(rerun_on_resume=True)`` parent (it
calls ``ctx.run_node``) while letting each ``build_research_workflow(...)`` call
bind its own clarifier/planner — the cleanest way to make the seam testable
without module-level mutable state.

CP1 placeholder: T1 only auto-approves (passthrough). The real
``RequestInput(response_schema=ResearchPlan)`` checkpoint lands in T4. The seam is
isolated in :func:`_cp1_autoapprove` so T4 can swap it cleanly.
"""
from __future__ import annotations

from typing import Optional

from google.adk import Context, Workflow
from google.adk.workflow import START, node
from google.adk.workflow._base_node import BaseNode

from .agents.clarifier import build_clarifier, parse_clarify_result
from .agents.planner import build_planner, parse_plan
from .llm import build_model
from .schemas import ResearchPlan


@node(rerun_on_resume=False)
async def _cp1_autoapprove(node_input: ResearchPlan) -> ResearchPlan:
    """Checkpoint 1 placeholder (T1 only) — auto-approve passthrough.

    Returns the planner's ``ResearchPlan`` unchanged, standing in for the human
    plan-approval checkpoint. T4 replaces this node with a
    ``@node(rerun_on_resume=False)`` that ``yield``s
    ``RequestInput(response_schema=ResearchPlan)`` so the user can edit the plan
    before research starts. Kept isolated here so that swap is a one-node change.
    """
    return node_input


def build_research_workflow(
    *,
    clarifier_node: Optional[BaseNode] = None,
    planner_node: Optional[BaseNode] = None,
) -> Workflow:
    """Build the research ``Workflow`` (the START→CP1 slice).

    The ``research`` parent node is created as a closure over the injected
    ``clarifier_node`` / ``planner_node`` so the same function body works for both
    the production agent-backed nodes and the canned test stubs.

    Args:
        clarifier_node: node run for the clarify stage. Defaults to the
            ``build_clarifier`` ``LlmAgent`` on the shared model. ``ctx.run_node``
            accepts an ``LlmAgent`` directly, so no wrapping is needed.
        planner_node: node run for the plan stage. Defaults to the
            ``build_planner`` ``LlmAgent`` on the shared model.

    Returns:
        A ``Workflow`` whose single edge runs the ``research`` node from START.
    """
    clarifier = clarifier_node if clarifier_node is not None else build_clarifier(model=build_model())
    planner = planner_node if planner_node is not None else build_planner(model=build_model())

    @node(rerun_on_resume=True)
    async def research(ctx: Context, node_input: str) -> ResearchPlan:
        """Drive the slice: clarify → plan → CP1, returning the approved plan.

        ``node_input`` is the raw user query: as the START node, ADK hands this
        node the user message and (state-binding mode) passes the ``node_input``
        parameter through directly, auto-converting the user ``Content`` to ``str``.
        The parameter is named ``node_input`` precisely so ADK's START entry
        binding supplies it — naming it anything else makes ADK look the value up
        in (empty) session state.

        Parent node that calls ``ctx.run_node`` — hence ``rerun_on_resume=True``
        (ADK re-runs the parent on resume so it can collect interrupted child
        results; completed children are skipped via auto-checkpointing).
        """
        # ADK serializes a node's BaseModel return via ``model_dump()``, so
        # ``run_node`` hands back a plain dict — coerce it back through the
        # agents' own parse helpers (idempotent on dict/str/model).
        clarified = parse_clarify_result(await ctx.run_node(clarifier, node_input))
        # ``parse_plan`` validates AND applies the deterministic depth policy
        # (``apply_depth_targets``), so the stop-rule targets stay config-driven.
        plan = parse_plan(await ctx.run_node(planner, clarified.normalized_query))

        # ── CP1 seam (T1 placeholder; T4 swaps in RequestInput) ──────────────
        approved = parse_plan(await ctx.run_node(_cp1_autoapprove, plan))
        return approved

    return Workflow(name="research", edges=[(START, research)])
