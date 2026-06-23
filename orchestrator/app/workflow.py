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

CP1 (T4): a real ``RequestInput`` plan-approval checkpoint. The isolated
``_cp1_checkpoint`` node ``yield``s ``RequestInput(response_schema=ResearchPlan)``
to pause the run for the user; on resume the human-supplied (possibly edited)
plan becomes the node's output. T6 adds an optional ``SessionStore`` write-through
so clarify+plan land on disk during the slice (an additive safety net, demoted in
E3.S2 — see the note on the ``store`` seam below).
"""
from __future__ import annotations

from typing import Optional

from google.adk import Context, Workflow
from google.adk.events import RequestInput
from google.adk.workflow import START, node
from google.adk.workflow._base_node import BaseNode

from .agents.clarifier import build_clarifier, parse_clarify_result
from .agents.planner import build_planner, parse_plan
from .llm import build_model
from .schemas import ResearchPlan
from .session import SessionStore


def _cp1_summary(plan: ResearchPlan) -> str:
    """Human-readable plan summary for the CP1 prompt.

    Ports the ``=== Checkpoint 1: Research Plan ===`` block from
    :func:`app.checkpoint.cp1_checkpoint` (the v1 console gate) so the same
    information the user saw at the stdin checkpoint now rides on the
    ``RequestInput(message=...)`` shown by the ADK HITL client. Kept as a tiny
    pure helper so the node body stays about control flow, not formatting.
    """
    lines = [
        "=== Checkpoint 1: Research Plan ===",
        f"depth={plan.depth.value}  subtopics={len(plan.subtopics)}  "
        f"seed_urls={len(plan.seed_urls)}",
    ]
    lines += [
        f"  - [{st.id}] {st.question} (target_evidence={st.target_evidence})"
        for st in plan.subtopics
    ]
    return "\n".join(lines)


@node(rerun_on_resume=False)
async def _cp1_checkpoint(node_input: ResearchPlan):
    """Checkpoint 1 (T4) — block for human plan approval via ``RequestInput``.

    ``node_input`` is the planner's ``ResearchPlan`` (passed by the parent as
    ``ctx.run_node(_cp1_checkpoint, plan)``). The node ``yield``s a
    ``RequestInput`` carrying the ported plan summary (``message``), the plan
    itself (``payload``), and ``response_schema=ResearchPlan`` — which pauses the
    workflow. ADK validates the human's resume response against that schema, so
    the value the parent receives back from ``ctx.run_node`` is the approved
    (possibly edited) plan as a dict.

    ``rerun_on_resume=False``: this node does not re-execute on resume — the
    framework treats the injected response as the node's output directly. (The
    parent ``research`` node, which *calls* ``ctx.run_node``, is the one that
    must be ``rerun_on_resume=True``.)
    """
    yield RequestInput(
        message=_cp1_summary(node_input),
        payload=node_input,
        response_schema=ResearchPlan,
    )


def build_research_workflow(
    *,
    clarifier_node: Optional[BaseNode] = None,
    planner_node: Optional[BaseNode] = None,
    store: Optional[SessionStore] = None,
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
        store: optional :class:`~app.session.SessionStore` — when provided, the
            ``research`` node write-throughs the clarify+plan artifacts to
            ``data/sessions/{id}/`` during the run (T6). This is an **additive
            safety net**, NOT an execution override: it never gates or replaces
            ADK's own auto-checkpointed resume state, it only mirrors the plan to
            disk so a run is inspectable/recoverable outside the ADK event log.
            **Demoted in E3.S2** once ADK persistence is the single source of
            truth. ``None`` (default) = no disk write, so case (a) is unchanged.

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

        # ── T6 write-through safety net (additive; demoted in E3.S2) ─────────
        # Mirror the planned slice to disk so the run is inspectable/recoverable
        # outside ADK's event log. This is NOT an execution override — it only
        # persists, it never re-reads to drive control flow.
        if store is not None:
            store.save_plan(plan)

        # ── CP1 (T4): real RequestInput plan-approval checkpoint ─────────────
        # ``_cp1_checkpoint`` yields RequestInput and pauses; on resume the
        # human-supplied plan comes back as a dict — coerce it via ``parse_plan``
        # (which also re-applies the depth policy to any edited subtopics).
        approved = parse_plan(await ctx.run_node(_cp1_checkpoint, plan))
        return approved

    return Workflow(name="research", edges=[(START, research)])
