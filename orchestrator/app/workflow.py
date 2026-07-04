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

import asyncio
import json
import logging
from typing import Optional

from google.adk import Context, Workflow
from google.adk.events import Event, RequestInput
from google.adk.workflow import START, node
from google.adk.workflow._base_node import BaseNode
from google.adk.workflow._errors import NodeInterruptedError

# Imported as MODULES (not symbols) so their ``_build_default_toolset`` /
# ``build_<x>`` attributes stay monkeypatchable in the node-path lifecycle tests
# (mirrors how ``pipeline`` reaches the agent factories for the Tier-0 fixtures).
from .agents import acquirer as acquirer_mod
from .agents import extractor as extractor_mod
from .agents import verifier as verifier_mod
from .agents.clarifier import build_clarifier, parse_clarify_result
from .agents.planner import build_planner, parse_plan
from .agents.writer import build_writer, render_report
from .citation import CitationClient
from .cp3_adapter import apply_cp3_verbs
from .jsonio import JSON_ONLY_REASK
from .llm import build_model
from .research_policy import depth_budget, merge_ledger, stop_rule
from .schemas import ClaimLedger, Depth, ResearchPlan, ScoredURL

logger = logging.getLogger("orchestrator.workflow")


# ── H1: invocation-scoped run-resource cache ───────────────────────────────────
# The ``research`` node is ``rerun_on_resume=True``: ADK replays it top-to-bottom
# on every HITL resume hop. Its OWNED crawl4ai toolsets/agents + citation client
# are plain node-body code (not checkpoint-skippable children), so a naive build
# re-runs every hop; and because ``ctx.run_node`` raises ``NodeInterruptedError``
# (a BaseException, agents/context.py:523) up through the loop's cleanup, a paused
# hop CLOSED them — forcing a full rebuild + crawl4ai subprocess re-spawn on
# resume. We cache them per ``invocation_id`` and REUSE across pause/resume,
# releasing (closing) them only on the run's normal completion or a real error —
# never on a HITL pause. Also keyed on the building event loop so a resume on a
# different loop rebuilds safely rather than reusing a loop-bound session.
class _RunResources:
    """Per-invocation owned resources for the ``research`` node (H1)."""

    __slots__ = (
        "acquirer",
        "extractor",
        "verifier",
        "owned_toolsets",
        "citation_client",
        "loop",
    )

    def __init__(
        self, *, acquirer, extractor, verifier, owned_toolsets, citation_client, loop
    ) -> None:
        self.acquirer = acquirer
        self.extractor = extractor
        self.verifier = verifier
        self.owned_toolsets = owned_toolsets
        self.citation_client = citation_client
        self.loop = loop

    async def aclose(self) -> None:
        # Close each OWNED toolset once (idempotent in ADK; injected nodes have
        # none). The shared stdio session manager (H2) is closed by whichever
        # owned toolset holds it; subsequent closes are no-ops.
        for ts in self.owned_toolsets:
            await ts.close()
        await self.citation_client.aclose()


_RUN_RESOURCES: dict[str, _RunResources] = {}


async def _release_run_resources(cache_key: str) -> None:
    """Pop and close the cached resources for ``cache_key`` (safe if absent)."""
    res = _RUN_RESOURCES.pop(cache_key, None)
    if res is not None:
        await res.aclose()

# CP3 verb grammar first-tokens (see cp3_adapter.apply_cp3_verbs). Used only to
# flag reply lines that match no verb — apply_cp3_verbs stays the single source
# of truth for actually applying them.
_CP3_VERBS = frozenset({"+", "-", "r", "redirect", "d", "done"})


def _unrecognized_cp3_lines(reply: str) -> list[str]:
    """Non-empty lines in a CP3 reply whose first token is not a known verb."""
    unrecognized: list[str] = []
    for line in reply.splitlines():
        action = line.strip()
        if not action:
            continue
        verb = action.partition(" ")[0].lower()
        if verb not in _CP3_VERBS:
            unrecognized.append(action)
    return unrecognized


async def _acquire_urls(
    ctx: Context, acquirer: object, payload: str
) -> tuple[list[ScoredURL], Optional[str]]:
    """Run the Acquirer node and coerce its output, with ONE bounded JSON re-ask.

    The v1 live path routed the Acquirer through ``jsonio.invoke_json_with_retry``
    (E0.S2) but the node path bypassed it — so in production a prose answer (the
    model "answering the question" instead of tool-calling) silently degraded to
    ``urls=[]``. This restores the corrective re-ask at the node seam: if the
    first output carries no parseable ScoredURL JSON, re-run the node once with
    the :data:`JSON_ONLY_REASK` suffix. Resume-deterministic: the retry decision
    derives from the first node's PERSISTED output, so a replay takes the same
    branch and the schedule stays aligned.

    Returns ``(urls, None)`` on success or ``([], error_message)`` after the
    re-ask also fails — the caller emits the failure into session state instead
    of masking it.
    """
    try:
        return acquirer_mod.parse_scored_urls(await ctx.run_node(acquirer, payload)), None
    except ValueError as first_err:
        logger.warning("acquirer output not parseable, issuing JSON-only re-ask: %s", first_err)
        try:
            return (
                acquirer_mod.parse_scored_urls(
                    await ctx.run_node(acquirer, payload + JSON_ONLY_REASK)
                ),
                None,
            )
        except ValueError as retry_err:
            logger.error("acquirer re-ask still not parseable: %s", retry_err)
            return [], f"first: {first_err}; reask: {retry_err}"

# ── Reject signal (E3.S1 T3) ─────────────────────────────────────────────────
# The v1 console gates raised ``CheckpointRejected`` on a reject; ``run_pipeline``
# caught it and left the session at its last stage — aborted but resumable
# (``checkpoint.py:37``, ``pipeline.py:303``). The v2 node path has no exception
# channel that preserves resumability: a node that *raises* is recorded
# ``NodeStatus.FAILED`` (``_node_runner.py:139`` sets ``ctx._error`` →
# ``_dynamic_node_scheduler._record_result`` → FAILED), which is terminal, NOT a
# resumable pause. The only resumable-pause outcome in ADK 2.3.0 is a node that
# yields a ``RequestInput`` interrupt (``NodeStatus.WAITING``). So a "reject =
# resumable abort at the rejecting checkpoint" is implemented the ADK-native way:
# the reject reply is a reserved sentinel string, and on seeing it the parent
# RE-ISSUES a fresh ``RequestInput`` pause at the SAME logical checkpoint (a new
# per-attempt ``interrupt_id``) — exactly the "rejection/retry cycle" the ADK
# ``RequestInput.interrupt_id`` docstring sanctions. The run halts there,
# resumable by ``invocation_id``; resuming re-enters that checkpoint.
CHECKPOINT_REJECT = "__reject__"


def _is_str_reject(reply: str) -> bool:
    """True iff a ``response_schema=str`` reply (CP2/CP3) is the reject sentinel.

    Collision-safe: the reply is compared against the reserved token verbatim.
    A real CP2 edit is arbitrary markdown and a real CP3 reply is verb lines —
    neither is ever the bare literal ``"__reject__"`` (CP3 splits on whitespace,
    so the sentinel is its own line/verb and matches no ``+/-/r/d`` verb).
    """
    return reply.strip() == CHECKPOINT_REJECT


def _is_cp1_reject(raw: object) -> bool:
    """True iff a CP1 (``response_schema=ResearchPlan``) reply is the reject signal.

    CP1's reply MUST satisfy ``response_schema=ResearchPlan`` — ADK 2.3.0 validates
    the resume response against that schema at rehydration time
    (``_rehydration_utils._validate_resume_response``), BEFORE the parent sees it,
    so an arbitrary ``{"__reject__": ...}`` mapping is rejected by ADK itself and
    never reaches here. The reject signal therefore has to be a SCHEMA-VALID
    ``ResearchPlan`` that is still unambiguous: an **empty ``subtopics`` list**.
    ``ResearchPlan.subtopics`` has no ``min_length`` so ``{"subtopics": []}``
    validates, yet a real/edited plan ALWAYS carries at least one subtopic (a
    zero-subtopic plan has nothing to research) — so it can never collide with a
    legitimate approval/edit. Checked on the RAW ``run_node`` dict (the parent
    coerces a non-reject reply via ``parse_plan`` afterwards).
    """
    if isinstance(raw, ResearchPlan):
        return not raw.subtopics
    return isinstance(raw, dict) and raw.get("subtopics") == []


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


def _make_cp1_checkpoint():
    """Build a per-attempt CP1 ``RequestInput`` plan-approval node.

    A factory (returning a FRESH node each call) so a reject can re-issue a new
    pause at this checkpoint (T3's resumable-abort retry cycle): each
    ``ctx.run_node(_make_cp1_checkpoint(), plan)`` call auto-increments the node's
    ``run_id`` (``Context.run_node`` keys ``_child_run_counters`` by node name), so
    every attempt is a distinct ``node_path`` with its own default ``interrupt_id``
    UUID — the run pauses afresh each reject, resumable by ``invocation_id``. The
    node mirrors the original ``_cp1_checkpoint``: ``@node(rerun_on_resume=False)``
    yields ``RequestInput`` carrying the ported plan summary (``message``), the plan
    itself (``payload``), and ``response_schema=ResearchPlan`` — which pauses the
    workflow. ADK validates the human's resume response against that schema, so the
    value the parent receives back from ``ctx.run_node`` is the approved (possibly
    edited) plan as a dict (or the reject signal — a SCHEMA-VALID empty-``subtopics``
    plan, caught by the parent's ``_is_cp1_reject`` BEFORE ``parse_plan``; an
    arbitrary sentinel mapping can't be used under ``response_schema=ResearchPlan``
    because ADK validates the resume response against that schema first).

    ``rerun_on_resume=False``: this node does not re-execute on resume — the
    framework treats the injected response as the node's output directly. (The
    parent ``research`` node, which *calls* ``ctx.run_node``, is the one that
    must be ``rerun_on_resume=True``.)
    """

    @node(rerun_on_resume=False)
    async def _cp1_checkpoint(node_input: ResearchPlan):
        yield RequestInput(
            message=_cp1_summary(node_input),
            payload=node_input,
            response_schema=ResearchPlan,
        )

    return _cp1_checkpoint


def _cp2_summary(draft: str) -> str:
    """Human-readable draft header for the CP2 prompt.

    Ports the ``=== Checkpoint 2: Draft Report ===`` block from
    :func:`app.checkpoint.cp2_checkpoint` (the v1 console gate) — header line plus
    the ``({len(draft)} chars)`` size line — so the same information the user saw
    at the stdin checkpoint now rides on the ``RequestInput(message=...)`` shown by
    the ADK HITL client. Kept as a tiny pure helper so the node body stays about
    control flow, not formatting.
    """
    return "\n".join(
        [
            "=== Checkpoint 2: Draft Report ===",
            f"({len(draft)} chars)",
        ]
    )


def _make_cp2_checkpoint():
    """Build a per-attempt CP2 ``RequestInput`` draft-approval node.

    A factory (returning a FRESH node each call), like ``_make_cp1_checkpoint``,
    so a reject can re-issue a new pause at this checkpoint (T3's resumable-abort
    retry cycle): each ``ctx.run_node`` call auto-increments the node's ``run_id``,
    so every attempt is a distinct ``node_path``/``interrupt_id`` and the run
    pauses afresh each reject, resumable by ``invocation_id``.

    ``node_input`` is the rendered markdown ``draft`` (passed by the parent as
    ``ctx.run_node(_make_cp2_checkpoint(), draft)``). The node ``yield``s a
    ``RequestInput`` carrying the ported header (``message``), the draft itself
    (``payload``), and ``response_schema=str`` — which pauses the workflow.

    **Approve/edit resume contract** (verified vs ADK 2.3.0
    ``_rehydration_utils._unwrap_response``): the human's resume reply is the FINAL
    draft string. ADK wraps a ``response_schema=str`` reply as ``{"result": <str>}``
    and unwraps it back to the raw string, so the value the parent receives from
    ``ctx.run_node`` is whatever markdown the human sent. The parent treats an
    empty reply as *approve* (keep the draft unchanged) and a non-empty reply as
    *edit* (the reply IS the edited markdown, round-tripped verbatim). This mirrors
    CP1's resume contract (the human-supplied value becomes the node output) and
    the v1 ``cp2_checkpoint`` approve/edit semantics — approve returns the draft as
    is, edit replaces it with the saved text.

    **Reject (T3):** the reserved reply ``"__reject__"`` is the reject signal
    (``_is_str_reject``). It is collision-safe — a real edit is arbitrary markdown,
    never the bare sentinel. The parent treats it as a resumable abort by re-issuing
    a fresh CP2 pause (see the ``research`` body), so the run halts here resumably.

    ``rerun_on_resume=False``: this node does not re-execute on resume — the
    framework treats the injected response as the node's output directly (the
    parent ``research`` node, which *calls* ``ctx.run_node``, is the one that is
    ``rerun_on_resume=True``).
    """

    @node(rerun_on_resume=False)
    async def _cp2_checkpoint(node_input: str):
        yield RequestInput(
            message=_cp2_summary(node_input),
            payload=node_input,
            response_schema=str,
        )

    return _cp2_checkpoint


def _cp3_summary(urls: list[ScoredURL]) -> str:
    """Human-readable candidate-source list for the CP3 prompt.

    Ports the ``=== Checkpoint 3: Candidate Sources ===`` block from
    :func:`app.checkpoint.cp3_checkpoint` so the indexed list (the ``[n]`` indices
    the human references in ``- <n>``) and the verb legend ride on the
    ``RequestInput(message=...)`` shown by the ADK HITL client.
    """
    lines = ["=== Checkpoint 3: Candidate Sources ==="]
    lines += [
        f"  [{i}] {u.score:.2f}  {u.etld1 or '?'}  {u.url}"
        for i, u in enumerate(urls)
    ]
    lines.append("[+] add <url> / [-] exclude <n> / [r]edirect <url> / [d]one:")
    return "\n".join(lines)


def _make_cp3_checkpoint(interrupt_id: str):
    """Build a per-pass CP3 ``RequestInput`` node bound to ``interrupt_id``.

    A factory (not a module-level node like ``_cp1_checkpoint``) because each loop
    pass needs a **unique** ``interrupt_id`` — ``f"cp3_{subtopic.id}_{iteration}"``
    — so ADK matches each pass's function-call/response pair correctly across
    resumes (verified against ADK 2.3.0: ``interrupt_id`` is a direct constructor
    arg on ``google.adk.events.RequestInput``). The node mirrors
    ``_cp1_checkpoint``: ``@node(rerun_on_resume=False)`` yields ``RequestInput``
    to pause; on resume the human's free-text reply becomes the node's output
    (``response_schema=str``), which the parent feeds to ``apply_cp3_verbs``.

    ``node_input`` is the candidate ``list[ScoredURL]`` (passed by the parent as
    ``ctx.run_node(cp3, urls)``); the message carries the indexed list + verb
    legend, the payload carries the URLs themselves.
    """

    @node(rerun_on_resume=False)
    async def _cp3_checkpoint(node_input: list[ScoredURL]):
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=_cp3_summary(node_input),
            payload=node_input,
            response_schema=str,
        )

    return _cp3_checkpoint


def build_research_workflow(
    *,
    clarifier_node: Optional[BaseNode] = None,
    planner_node: Optional[BaseNode] = None,
    acquirer_node: Optional[BaseNode] = None,
    extractor_node: Optional[BaseNode] = None,
    verifier_node: Optional[BaseNode] = None,
    writer_node: Optional[BaseNode] = None,
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
        acquirer_node: node run for the per-subtopic Acquire phase. Defaults to the
            ``build_acquirer`` ``LlmAgent``. ``ctx.run_node`` accepts an ``LlmAgent``
            directly, so no wrapping is needed.
        extractor_node: node run for the Extract phase. Defaults to
            ``build_extractor``; its output is discarded (it crawls into ChromaDB,
            handing back page-ids the loop does not consume — as in v1).
        verifier_node: node run for the Verify phase. Defaults to
            ``build_verifier``. Its raw output is re-enriched via
            :func:`enrich_ledger` (the node path skips v1's auto-enrich).

        writer_node: node run AFTER the research loop to draft the markdown
            report (T0). Defaults to the ``build_writer`` ``LlmAgent``. The Writer
            is **reasoning-only and holds NO toolset** — it is resolved at build
            time (like clarifier/planner) and is NOT added to ``owned_toolsets``.
            It has no ``output_schema``, so ``ctx.run_node`` returns its emitted
            markdown as a plain ``str`` body, which :func:`render_report` wraps
            with the deterministic coverage/contradictions/sources skeleton.

    CP3 (E3.S1 T2) is a real deep-only ``RequestInput`` checkpoint wired
    in-loop between acquire and extract (see ``_make_cp3_checkpoint`` and the
    loop body) — not a DI param. It only fires when ``approved.depth ==
    Depth.DEEP``, so shallow/normal runs are byte-identical to before.

    Returns:
        A ``Workflow`` whose single edge runs the ``research`` node from START.
    """
    # Only clarifier/planner are resolved at build time — they hold NO toolsets.
    # The acquirer/extractor/verifier (which OWN crawl4ai MCP toolsets) are built
    # once per RUN at loop entry inside the ``research`` node so a single toolset
    # is shared across all passes and closed in a ``finally`` (T4).
    clarifier = clarifier_node if clarifier_node is not None else build_clarifier(model=build_model())
    planner = planner_node if planner_node is not None else build_planner(model=build_model())
    # The Writer (T0) is reasoning-only and OWNS NO toolset, so — like
    # clarifier/planner — it is resolved here at build time and is never added
    # to ``owned_toolsets`` (no build/close lifecycle for it).
    writer = writer_node if writer_node is not None else build_writer()

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

        # ── CP1 (T4): real RequestInput plan-approval checkpoint ─────────────
        # ``_make_cp1_checkpoint()`` yields RequestInput and pauses; on resume the
        # human-supplied plan comes back as a dict — coerce it via ``parse_plan``
        # (which also re-applies the depth policy to any edited subtopics).
        #
        # ── CP1 reject (T3): resumable abort ─────────────────────────────────
        # A reject reply rides as a SCHEMA-VALID empty-``subtopics`` plan (ADK
        # validates the resume response against ``response_schema=ResearchPlan``
        # first, so a free-form sentinel mapping can't be used here — see
        # ``_is_cp1_reject``). Detected on the RAW run_node output BEFORE
        # ``parse_plan``. On reject we re-issue a FRESH CP1 pause (a new node
        # instance → new run_id/interrupt_id → the run halts again at this
        # checkpoint, resumable by ``invocation_id``). The loop continues until the
        # human approves/edits a real (non-empty) plan.
        while True:
            cp1_reply = await ctx.run_node(_make_cp1_checkpoint(), plan)
            if not _is_cp1_reject(cp1_reply):
                break
        approved = parse_plan(cp1_reply)
        # ── E3.S2 T1: emit approved plan into ADK session state ──────────────
        yield Event(state={"v2_plan": approved.model_dump(mode="json")})

        # ── Toolset-once + resume-idempotent lifecycle (E2.S2 T4 · H1 · H2) ───
        # Build the OWNED crawl4ai toolsets/agents + citation client ONCE per run
        # and cache them by ``invocation_id`` (see ``_RUN_RESOURCES``) so a HITL
        # resume hop REUSES them instead of rebuilding + re-spawning the crawl4ai
        # subprocess (H1). Released only on normal completion / real error, never
        # on a pause. Ownership rule unchanged: an INJECTED node is caller-owned,
        # so we build/close NO toolset for it; only a defaulted agent owns its
        # toolset. H2: the defaulted toolsets ADOPT one shared stdio session
        # manager (the first real toolset's), collapsing 3 subprocesses to 1 while
        # each keeps its own ``tool_filter`` (per-agent least-privilege preserved).
        cache_key = ctx.invocation_id
        running_loop = asyncio.get_running_loop()
        _res = _RUN_RESOURCES.get(cache_key)
        if _res is not None and _res.loop is running_loop:
            acquirer = _res.acquirer
            extractor = _res.extractor
            verifier = _res.verifier
            owned_toolsets = _res.owned_toolsets
            citation_client = _res.citation_client
        else:
            if _res is not None:
                # Stale entry from a different event loop — release then rebuild.
                await _release_run_resources(cache_key)
            owned_toolsets = []
            _shared_mgr: list = []  # 0-or-1 element: the shared stdio session mgr

            def _adopt(ts):
                """Give a real ``McpToolset`` the shared stdio session manager (H2).

                Fake/injected toolsets (offline lifecycle tests) lack
                ``_mcp_session_manager`` and are left untouched, so those tests
                still observe their own per-build fakes closed exactly once.
                """
                if hasattr(ts, "_mcp_session_manager"):
                    if _shared_mgr:
                        ts._mcp_session_manager = _shared_mgr[0]
                    else:
                        _shared_mgr.append(ts._mcp_session_manager)
                return ts

            if acquirer_node is not None:
                acquirer = acquirer_node
            else:
                acquirer_ts = _adopt(acquirer_mod._build_default_toolset())
                acquirer = acquirer_mod.build_acquirer(acquirer_ts)
                owned_toolsets.append(acquirer_ts)
            if extractor_node is not None:
                extractor = extractor_node
            else:
                extractor_ts = _adopt(extractor_mod._build_default_toolset())
                extractor = extractor_mod.build_extractor(extractor_ts)
                owned_toolsets.append(extractor_ts)
            if verifier_node is not None:
                verifier = verifier_node
            else:
                verifier_ts = _adopt(verifier_mod._build_default_toolset())
                verifier = verifier_mod.build_verifier(toolset=verifier_ts)
                owned_toolsets.append(verifier_ts)

            # Citation-BFS enrichment (architecture.md §11): one client for the
            # whole run, cached/closed alongside the toolsets. ``enrich_with_
            # citations`` no-ops unless ``should_run_citation_bfs`` passes.
            citation_client = CitationClient()
            _RUN_RESOURCES[cache_key] = _RunResources(
                acquirer=acquirer,
                extractor=extractor,
                verifier=verifier,
                owned_toolsets=owned_toolsets,
                citation_client=citation_client,
                loop=running_loop,
            )

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
                    # ── E3.S2 T1: emit loop progress into ADK session state ───
                    yield Event(
                        state={
                            "v2_current_subtopic_id": subtopic.id,
                            "v2_iteration": iteration,
                            "v2_budget": budget,
                        }
                    )
                    before = len(ledger.claims)

                    acquire_payload = acquirer_mod.build_acquirer_prompt(
                        subtopic.question,
                        depth=approved.depth,
                        source_classes=subtopic.source_classes,
                    )
                    urls, acquire_error = await _acquire_urls(ctx, acquirer, acquire_payload)
                    if acquire_error is not None:
                        yield Event(
                            state={
                                "v2_acquire_failure": {
                                    "subtopic_id": subtopic.id,
                                    "iteration": iteration,
                                    "error": acquire_error,
                                }
                            }
                        )
                    urls = await acquirer_mod.enrich_with_citations(
                        urls,
                        subtopic_question=subtopic.question,
                        depth=approved.depth,
                        source_classes=subtopic.source_classes,
                        client=citation_client,
                    )

                    # ── CP3 (E3.S1 T2): real deep-only RequestInput checkpoint ─
                    # The seam between acquire and extract. Gated on
                    # ``approved.depth == DEEP`` (mirrors v1 ``next_phase``
                    # MID_ACQUIRE deep-gating); shallow/normal skip it byte-for-
                    # byte. The CP3 node yields ``RequestInput`` (unique
                    # interrupt_id ``f"cp3_{subtopic.id}_{iteration}"``) and pauses;
                    # on resume the human's free-text verb reply comes back as a
                    # ``str``, which ``apply_cp3_verbs`` turns into
                    # ``(filtered_urls, needs_supplemental)``.
                    if approved.depth == Depth.DEEP:
                        # ── CP3 reject (T3): resumable abort ─────────────────
                        # v1's CP3 had NO reject verb (``checkpoint.py:127-141``),
                        # but T3 mandates reject cover CP3, so we add the reserved
                        # ``"__reject__"`` reply (``_is_str_reject``) — collision-
                        # safe with the ``+/-/r/d`` verb grammar. On reject we
                        # re-issue a FRESH CP3 pause with an attempt-suffixed
                        # interrupt_id (first attempt keeps the original
                        # ``cp3_{id}_{iter}`` id so existing tests are unaffected),
                        # halting resumably at this checkpoint until a non-reject
                        # verb reply arrives.
                        cp3_attempt = 0
                        while True:
                            cp3_iid = f"cp3_{subtopic.id}_{iteration}"
                            if cp3_attempt:
                                cp3_iid = f"{cp3_iid}_{cp3_attempt}"
                            cp3 = _make_cp3_checkpoint(cp3_iid)
                            reply = str(await ctx.run_node(cp3, urls) or "")
                            if not _is_str_reject(reply):
                                break
                            cp3_attempt += 1
                        # apply_cp3_verbs silently ignores lines that match no verb;
                        # log them here so an unexpected reply shape is visible rather
                        # than degrading invisibly to a no-op.
                        unrecognized = _unrecognized_cp3_lines(reply)
                        if unrecognized:
                            logger.warning(
                                "CP3 reply for subtopic %s (iter %s) had unrecognized lines: %r",
                                subtopic.id,
                                iteration,
                                unrecognized,
                            )
                        urls, needs_supplemental = apply_cp3_verbs(urls, reply)

                        # ── Supplemental re-entry (v2 analog of v1 appending
                        # ``ResearchPhase.ACQUIRE``): when the user added/redirected
                        # a URL, re-run the Acquirer ONCE in this same pass to crawl
                        # the steered targets, merging its candidates with the
                        # user-filtered list. This extra ``run_node`` is appended to
                        # the deterministic schedule (always after the CP3 node,
                        # only when supplemental) so ADK execution-IDs stay aligned
                        # on resume; the merged list is what feeds extract.
                        if needs_supplemental:
                            supplemental, supp_error = await _acquire_urls(
                                ctx, acquirer, acquire_payload
                            )
                            if supp_error is not None:
                                yield Event(
                                    state={
                                        "v2_acquire_failure": {
                                            "subtopic_id": subtopic.id,
                                            "iteration": iteration,
                                            "supplemental": True,
                                            "error": supp_error,
                                        }
                                    }
                                )
                            supplemental = await acquirer_mod.enrich_with_citations(
                                supplemental,
                                subtopic_question=subtopic.question,
                                depth=approved.depth,
                                source_classes=subtopic.source_classes,
                                client=citation_client,
                            )
                            urls = urls + supplemental

                    # Extract crawls into ChromaDB; page-ids are discarded (as v1).
                    await ctx.run_node(
                        extractor,
                        json.dumps([u.model_dump(mode="json") for u in urls]),
                    )

                    # The node path calls the verifier agent directly, which does
                    # NOT auto-enrich — so re-apply ``enrich_ledger`` to match v1's
                    # status/confidence recompute (v1 ``verify`` always re-enriches).
                    # Guard: MCP search_chunks timeout leaves the tool-agent with no
                    # JSON to return (no output_schema in tool mode). Mirror the same
                    # try/except that verifier.verify() uses (verifier.py:563-569).
                    try:
                        new = verifier_mod.enrich_ledger(
                            verifier_mod.parse_ledger(
                                await ctx.run_node(verifier, subtopic.question)
                            )
                        )
                    except ValueError as verify_err:
                        # Degrade to an empty ledger (as before) but surface the
                        # failure loudly instead of swallowing it — a verifier that
                        # answers in prose / times out must be visible in the trace.
                        logger.error(
                            "verifier output not parseable for subtopic %s (iter %s): %s",
                            subtopic.id,
                            iteration,
                            verify_err,
                        )
                        yield Event(
                            state={
                                "v2_verify_failure": {
                                    "subtopic_id": subtopic.id,
                                    "iteration": iteration,
                                    "error": str(verify_err),
                                }
                            }
                        )
                        new = ClaimLedger(claims=[])
                    ledger = merge_ledger(ledger, new)

                    after = len(ledger.claims)
                    new_claims = after - before
                    iteration += 1
                # ── E3.S2 T1 / M3: emit accumulated ledger into ADK session
                # state ONCE per subtopic (milestone), not every inner-loop
                # iteration — avoids O(n²) re-serialization and one DB event +
                # ``claim_ledger.json`` rewrite per pass. The final subtopic's
                # emission doubles as the run-end flush.
                yield Event(state={"v2_ledger": ledger.model_dump(mode="json")})
        except NodeInterruptedError:
            # HITL pause (e.g. a CP3 ``RequestInput`` inside the loop): KEEP the
            # cached resources — and the shared crawl4ai subprocess — alive so the
            # resume hop reuses them instead of rebuilding/re-spawning (H1). ADK
            # raises this BaseException from ``ctx.run_node`` on pause; re-raise so
            # the run suspends normally.
            raise
        except BaseException:
            # Real error (or cancellation): release the OWNED resources (idempotent
            # in ADK; injected nodes left open — caller owns their lifecycle), then
            # propagate.
            await _release_run_resources(cache_key)
            raise
        else:
            # Loop finished for this hop: release the OWNED resources.
            await _release_run_resources(cache_key)

        # ── T0: run the Writer + render the markdown draft ───────────────────
        # The Writer is reasoning-only (no toolset, no output_schema). Feed it the
        # approved ``ResearchPlan`` + accumulated ``ClaimLedger`` as a JSON payload
        # (same shape v1's ``_write_body`` used). With NO output_schema, ADK's
        # LlmAgent wrapper sets ``event.output`` to the concatenated text parts —
        # a plain ``str`` — so ``ctx.run_node`` hands back the markdown body as a
        # string (verified vs ADK 2.3.0 ``process_llm_agent_output``). Coerce to
        # ``str`` defensively, then wrap it with the deterministic skeleton via
        # ``render_report`` (which itself calls coverage_report/split_contradictions).
        writer_payload = json.dumps(
            {
                "plan": approved.model_dump(mode="json"),
                "ledger": ledger.model_dump(mode="json"),
            }
        )
        body_markdown = str(await ctx.run_node(writer, writer_payload) or "")
        draft = render_report(approved, ledger, body_markdown=body_markdown)

        # ── CP2 (T1): real RequestInput draft-approval checkpoint ────────────
        # Runs on EVERY path that reaches the Writer (unconditional — NOT deep-
        # gated). The ``_make_cp2_checkpoint()`` node yields RequestInput and pauses;
        # on resume the human's reply comes back as a ``str``. Approve = empty reply
        # (keep ``draft`` unchanged); edit = non-empty reply (the reply IS the
        # edited markdown, round-tripped verbatim).
        #
        # ── CP2 reject (T3): resumable abort ─────────────────────────────────
        # The reserved reply ``"__reject__"`` (``_is_str_reject``) is the reject
        # signal — collision-safe (a real edit is arbitrary markdown). On reject we
        # re-issue a FRESH CP2 pause (new node instance → new run_id/interrupt_id),
        # halting resumably at this checkpoint until an approve/edit reply arrives.
        while True:
            reply = str(await ctx.run_node(_make_cp2_checkpoint(), draft) or "")
            if not _is_str_reject(reply):
                break
        approved_draft = reply if reply else draft
        # ── E3.S2 T1: emit approved draft into ADK session state ─────────────
        yield Event(state={"v2_draft": approved_draft})

        # Terminal output: yield the ledger so the ADK framework emits it as the
        # node's output Event(output=ledger.model_dump()).  We use ``yield`` (not
        # ``return``) because adding state ``yield`` statements above converted
        # ``research`` into an async generator — ``return value`` is a SyntaxError
        # inside a generator in Python 3.
        yield ledger

    return Workflow(name="research", edges=[(START, research)])
