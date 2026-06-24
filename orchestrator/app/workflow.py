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

import json
from typing import Callable, Optional

from google.adk import Context, Workflow
from google.adk.events import RequestInput
from google.adk.workflow import START, node
from google.adk.workflow._base_node import BaseNode

# Imported as MODULES (not symbols) so their ``_build_default_toolset`` /
# ``build_<x>`` attributes stay monkeypatchable in the node-path lifecycle tests
# (mirrors how ``pipeline`` reaches the agent factories for the Tier-0 fixtures).
from .agents import acquirer as acquirer_mod
from .agents import extractor as extractor_mod
from .agents import verifier as verifier_mod
from .agents.clarifier import build_clarifier, parse_clarify_result
from .agents.planner import build_planner, parse_plan
from .llm import build_model
from .research_policy import depth_budget, merge_ledger, stop_rule
from .schemas import ClaimLedger, Depth, ResearchPlan, Subtopic
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
    acquirer_node: Optional[BaseNode] = None,
    extractor_node: Optional[BaseNode] = None,
    verifier_node: Optional[BaseNode] = None,
    cp3_hook: Optional[Callable[[Subtopic, int], None]] = None,
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

        acquirer_node: node run for the per-subtopic Acquire phase. Defaults to the
            ``build_acquirer`` ``LlmAgent``. ``ctx.run_node`` accepts an ``LlmAgent``
            directly, so no wrapping is needed.
        extractor_node: node run for the Extract phase. Defaults to
            ``build_extractor``; its output is discarded (it crawls into ChromaDB,
            handing back page-ids the loop does not consume — as in v1).
        verifier_node: node run for the Verify phase. Defaults to
            ``build_verifier``. Its raw output is re-enriched via
            :func:`enrich_ledger` (the node path skips v1's auto-enrich).

        cp3_hook: optional CP3 deep-only pass-through (T5). Called once per loop
            pass — *only* when the approved plan's depth is ``Depth.DEEP`` — as
            ``cp3_hook(subtopic, iteration)`` at the seam between acquire and
            extract (mirrors v1 ``next_phase`` MID_ACQUIRE deep-gating). This is a
            structural placeholder: the real ``RequestInput`` CP3 with the
            ``+add/-exclude/r/d`` adapter lands in E3.S1. ``None`` (default) = no
            hook, so shallow/normal runs are unchanged.

    Returns:
        A ``Workflow`` whose single edge runs the ``research`` node from START.
    """
    # Only clarifier/planner are resolved at build time — they hold NO toolsets.
    # The acquirer/extractor/verifier (which OWN crawl4ai MCP toolsets) are built
    # once per RUN at loop entry inside the ``research`` node so a single toolset
    # is shared across all passes and closed in a ``finally`` (T4).
    clarifier = clarifier_node if clarifier_node is not None else build_clarifier(model=build_model())
    planner = planner_node if planner_node is not None else build_planner(model=build_model())

    @node(rerun_on_resume=True)
    async def research(ctx: Context, node_input: str) -> ClaimLedger:
        """Drive the slice: clarify → plan → CP1 → research loop → ledger.

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

        # ── Toolset-once / close() lifecycle (E2.S2 T4) ──────────────────────
        # Build each OWNED crawl4ai MCPToolset exactly ONCE per run here at loop
        # entry, share it across every acquire/extract/verify pass, and close it
        # in the ``finally`` below. This delivers the once-per-run lifecycle that
        # v1 could not (each v1 ``_drive`` ran under its own ``asyncio.run``, so a
        # stdio toolset was bound to a single drive call). Ownership rule mirrors
        # ``pipeline._drive_extractor``: an INJECTED node is caller-owned, so we
        # build/close NO toolset for it; only a defaulted agent owns its toolset.
        owned_toolsets = []
        if acquirer_node is not None:
            acquirer = acquirer_node
        else:
            acquirer_ts = acquirer_mod._build_default_toolset()
            acquirer = acquirer_mod.build_acquirer(acquirer_ts)
            owned_toolsets.append(acquirer_ts)
        if extractor_node is not None:
            extractor = extractor_node
        else:
            extractor_ts = extractor_mod._build_default_toolset()
            extractor = extractor_mod.build_extractor(extractor_ts)
            owned_toolsets.append(extractor_ts)
        if verifier_node is not None:
            verifier = verifier_node
        else:
            verifier_ts = verifier_mod._build_default_toolset()
            verifier = verifier_mod.build_verifier(toolset=verifier_ts)
            owned_toolsets.append(verifier_ts)

        # ── Research loop (E2.S2 T2+T3): port of v1 ``_run_research`` ─────────
        # ONE store-backed accumulator across ALL subtopics; subtopics run
        # sequentially (single-box, one hot model). Deterministic schedule order
        # (acquire → extract → verify, same sequence every replay) so ADK's
        # auto-generated execution IDs align on resume — NO custom run_id.
        budget = depth_budget(approved.depth)
        ledger = ClaimLedger(claims=[])
        try:
            for subtopic in approved.subtopics:
                iteration = 0
                new_claims = 0
                while not stop_rule(
                    ledger,
                    subtopic,
                    iteration=iteration,
                    new_claims=new_claims,
                    budget=budget,
                ):
                    before = len(ledger.claims)

                    acquire_payload = json.dumps(
                        {
                            "subtopic": subtopic.model_dump(mode="json"),
                            "depth": approved.depth.value,
                        }
                    )
                    urls = acquirer_mod.parse_scored_urls(
                        await ctx.run_node(acquirer, acquire_payload)
                    )

                    # ── T5: CP3 deep-only pass-through hook ───────────────────
                    # Structural seam between acquire and extract. Gated on
                    # ``approved.depth == DEEP`` (mirrors v1 ``next_phase``
                    # MID_ACQUIRE deep-gating); shallow/normal skip it. No
                    # ``RequestInput`` yet — the real CP3 with the
                    # ``+add/-exclude/r/d`` adapter is E3.S1. When that lands it
                    # must use a unique interrupt_id per pass:
                    # ``f"cp3_{subtopic.id}_{iteration}"``.
                    if approved.depth == Depth.DEEP and cp3_hook is not None:
                        cp3_hook(subtopic, iteration)

                    # Extract crawls into ChromaDB; page-ids are discarded (as v1).
                    await ctx.run_node(
                        extractor,
                        json.dumps([u.model_dump(mode="json") for u in urls]),
                    )

                    # The node path calls the verifier agent directly, which does
                    # NOT auto-enrich — so re-apply ``enrich_ledger`` to match v1's
                    # status/confidence recompute (v1 ``verify`` always re-enriches).
                    new = verifier_mod.enrich_ledger(
                        verifier_mod.parse_ledger(await ctx.run_node(verifier, subtopic.question))
                    )
                    ledger = merge_ledger(ledger, new)

                    after = len(ledger.claims)
                    new_claims = after - before
                    iteration += 1
        finally:
            # Close each OWNED toolset once (idempotent in ADK; injected nodes
            # left open — caller owns their lifecycle). Runs even if the loop raised.
            for ts in owned_toolsets:
                await ts.close()

        # ── T6 write-through safety net (additive; demoted in E3.S2) ─────────
        if store is not None:
            store.save_ledger(ledger)

        return ledger

    return Workflow(name="research", edges=[(START, research)])
