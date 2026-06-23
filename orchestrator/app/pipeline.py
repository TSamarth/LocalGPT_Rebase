"""
Composition root — wires the real agents + checkpoints into the Orchestrator (T4.2).

The orchestrator (orchestrator.py) is deliberately agnostic: it walks the stage
machine and calls handlers, but does not know about any concrete agent. This module
is where Story 5 goes live — it registers:

* **stage handlers** — CLARIFY→Clarifier, PLAN→Planner, SYNTHESIZE→coverage check,
  WRITE→Writer (RESEARCH is driven by ``Orchestrator._run_research`` directly);
* **post handlers** — CP1 after PLAN, CP2 after WRITE (Story 4 carry-forward);
* **phase handlers** — ACQUIRE/EXTRACT/VERIFY inside the research loop, plus CP3 on
  MID_ACQUIRE (deep plans only).

Sync↔async bridge: the orchestrator's ``Handler`` contract is synchronous, but the
agent entrypoints are ``async``. Each live handler bridges with :func:`_run_sync`
(``asyncio.run``) — every handler is a top-level run on the linear driver, so there
is no nested-loop hazard.

Offline-first: every external dependency (agent runner, MCP toolset, citation
client) is injectable via :class:`PipelineDeps`. With all defaults left ``None`` the
handlers drive the real ADK agents over Ollama + crawl4ai MCP (the live path);
tests pass canned runners + fake/no toolsets to run the whole pipeline offline.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Coroutine, Optional, TypeVar

from .agents import acquirer, clarifier, extractor, planner, verifier
from .agents.writer import build_writer, coverage_report, render_report
from .checkpoint import CheckpointRejected, cp1_handler, cp2_handler, cp3_handler
from .orchestrator import Orchestrator
from .schemas import ClaimLedger, ResearchPlan, ScoredURL, Stage
from .session import SessionStore
from .stage_machine import ResearchPhase

# An async callable driving the Extractor over a triaged URL list (live: crawl;
# offline test: a no-op or canned double).
ExtractRunner = Callable[[list[ScoredURL]], Coroutine[Any, Any, None]]
# An async callable returning the Writer's body markdown for (plan, ledger).
WriteRunner = Callable[[ResearchPlan, ClaimLedger], Awaitable[str]]


@dataclass
class PipelineDeps:
    """Injectable seams for the pipeline. All ``None`` ⇒ the live ADK path.

    Tests fill these with canned async runners + fake/None toolsets so the whole
    INTAKE→DONE walk runs offline.
    """
    clarify_runner: Optional[clarifier.RunnerFn] = None
    plan_runner: Optional[planner.PlannerRunner] = None
    acquire_runner: Optional[acquirer.AcquirerRunner] = None
    extract_runner: Optional[ExtractRunner] = None
    verify_runner: Optional[verifier.VerifierRunner] = None
    write_runner: Optional[WriteRunner] = None
    acquirer_citation_client: object = None  # CitationClient or fake
    acquirer_scorer: object = None           # RelevanceScorer or None
    extractor_toolset: object = None         # BaseToolset or None (→ default)
    verifier_toolset: object = None          # BaseToolset or None (→ default)


_T = TypeVar("_T")


def _run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Bridge an async agent call into the synchronous Handler contract."""
    return asyncio.run(coro)


# ── live ADK driver (for agents without their own entrypoint: Extractor, Writer) ──
async def _drive(agent, app_name: str, text: str, output_key: str) -> str:
    """Run a built agent once over ``text`` and return its output.

    Mirrors the agents' own ``_default_runner`` shape: prefer the structured value
    the agent wrote to session state under ``output_key``; fall back to the final
    response text. Imported lazily so offline tests never need ADK/Ollama.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name, user_id="orchestrator", session_id="pipe"
    )
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=text)])

    final_text = ""
    for event in runner.run(
        user_id="orchestrator", session_id="pipe", new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)

    session = await session_service.get_session(
        app_name=app_name, user_id="orchestrator", session_id="pipe"
    )
    if session is not None:
        stored = session.state.get(output_key)
        if isinstance(stored, (list, dict)):
            return json.dumps(stored)
        if isinstance(stored, str) and stored.strip():
            return stored
    return final_text


# ── ledger merge (research loop accumulation) ────────────────────────────────────
def _merge_ledger(store: SessionStore, new_ledger: ClaimLedger) -> None:
    """Append claims from ``new_ledger`` into the persisted ledger, deduped by id."""
    current = store.load_ledger()
    seen = {c.id for c in current.claims}
    for claim in new_ledger.claims:
        if claim.id not in seen:
            current.claims.append(claim)
            seen.add(claim.id)
    store.save_ledger(current)


# ── stage handlers ───────────────────────────────────────────────────────────────
def _make_clarify_handler(deps: PipelineDeps):
    def handler(orch: Orchestrator) -> None:
        state = orch.store.load_stage()
        if state is None:
            return
        result = _run_sync(clarifier.clarify(state.raw_query, runner=deps.clarify_runner))
        orch.store.set_stage(
            Stage.CLARIFY,
            normalized_query=result.normalized_query or state.raw_query,
        )
    return handler


def _make_plan_handler(deps: PipelineDeps):
    def handler(orch: Orchestrator) -> None:
        state = orch.store.load_stage()
        query = (state.normalized_query if state else "") or (state.raw_query if state else "")
        result = _run_sync(planner.plan(query, runner=deps.plan_runner))
        orch.store.save_plan(result)
    return handler


def _synthesize_handler(orch: Orchestrator) -> None:
    """Deterministic coverage roll-up over the accumulated ledger (FR6.3).

    The ledger is the Writer's sole content input; this stage just surfaces whether
    every subtopic was covered so gaps are explicit before WRITE. Model-free.
    """
    plan = orch.store.load_plan()
    if plan is None:
        return
    coverage_report(plan, orch.store.load_ledger())  # raises nothing; gaps go to Writer


def _make_write_handler(deps: PipelineDeps):
    def handler(orch: Orchestrator) -> None:
        plan = orch.store.load_plan() or ResearchPlan(subtopics=[])
        ledger = orch.store.load_ledger()
        body = _run_sync(_write_body(plan, ledger, deps))
        orch.store.save_draft(render_report(plan, ledger, body_markdown=body))
    return handler


async def _write_body(plan: ResearchPlan, ledger: ClaimLedger, deps: PipelineDeps) -> str:
    """The Writer agent's prose body (the deterministic skeleton is added by
    ``render_report``). Injectable; live path drives the Writer over ADK."""
    if deps.write_runner is not None:
        return await deps.write_runner(plan, ledger)
    payload = json.dumps(
        {"plan": plan.model_dump(mode="json"), "ledger": ledger.model_dump(mode="json")}
    )
    return await _drive(build_writer(), "writer", payload, "report_markdown")


# ── research-phase handlers ──────────────────────────────────────────────────────
def _make_acquire_handler(deps: PipelineDeps):
    def handler(orch: Orchestrator) -> None:
        subtopic = orch.current_subtopic
        plan = orch.store.load_plan()
        if subtopic is None or plan is None:
            return
        urls = _run_sync(
            acquirer.acquire(
                subtopic,
                plan,
                runner=deps.acquire_runner,
                citation_client=deps.acquirer_citation_client,
                scorer=deps.acquirer_scorer,
            )
        )
        orch.store.save_scored_urls(urls)
    return handler


def _make_extract_handler(deps: PipelineDeps):
    def handler(orch: Orchestrator) -> None:
        urls = orch.store.load_scored_urls()
        if not urls:
            return
        if deps.extract_runner is not None:
            _run_sync(deps.extract_runner(urls))
            return
        _run_sync(_drive_extractor(urls, deps.extractor_toolset))
    return handler


async def _drive_extractor(urls: list[ScoredURL], toolset) -> None:
    """Live path: drive the Extractor agent to crawl the URL list into ChromaDB.

    The agent maps each URL's ``strategy`` onto the matching crawl tool; we only
    need it to run, so the returned page IDs are discarded (downstream retrieval is
    by the Verifier's ``search_chunks``).

    Toolset lifecycle (Tier-0): when no toolset is injected we build the default
    crawl4ai ``MCPToolset`` here and ``close()`` it before returning, so each drive
    call cleans up its stdio subprocess instead of leaking one (the v1 sync bridge
    runs every handler under its own ``asyncio.run``, so a toolset cannot outlive a
    single drive call — a single long-lived toolset arrives with the v2 node path).
    An *injected* toolset is owned by the caller and is left open."""
    owns_toolset = toolset is None
    active = toolset if toolset is not None else extractor._build_default_toolset()
    try:
        agent = extractor.build_extractor(active)
        payload = json.dumps([u.model_dump(mode="json") for u in urls])
        await _drive(agent, "extractor", payload, "extracted_page_ids")
    finally:
        if owns_toolset:
            await active.close()


def _make_verify_handler(deps: PipelineDeps):
    runner = deps.verify_runner or _make_default_verify_runner(deps.verifier_toolset)

    def handler(orch: Orchestrator) -> None:
        subtopic = orch.current_subtopic
        if subtopic is None:
            return
        # Payload is the subtopic question; the Verifier retrieves chunks itself via
        # the search_chunks MCP tool, then deterministic policy is re-applied.
        new_ledger = _run_sync(verifier.verify(subtopic.question, runner=runner))
        _merge_ledger(orch.store, new_ledger)
    return handler


def _make_default_verify_runner(toolset):
    """Live Verifier runner that holds ``search_chunks`` (built lazily on first use).

    Toolset lifecycle (Tier-0): a default ``MCPToolset`` we build here is ``close()``d
    after the drive call so its stdio subprocess is not leaked; an injected toolset is
    the caller's to close. See ``_drive_extractor`` for why this is per-call in v1."""
    async def runner(payload: str) -> str:
        owns_toolset = toolset is None
        active = toolset if toolset is not None else verifier._build_default_toolset()
        try:
            agent = verifier.build_verifier(toolset=active)
            return await _drive(agent, verifier.AGENT_NAME, payload, verifier.OUTPUT_KEY)
        finally:
            if owns_toolset:
                await active.close()
    return runner


# ── composition root ─────────────────────────────────────────────────────────────
def build_orchestrator(
    query: str,
    *,
    deps: Optional[PipelineDeps] = None,
    register_checkpoints: bool = True,
    session_id: Optional[str] = None,
) -> Orchestrator:
    """Build a fully-wired Orchestrator (the deferred Story-4 composition root).

    Pass ``session_id`` to resume an existing run; otherwise a fresh session starts.
    ``deps`` injects offline doubles; ``register_checkpoints=False`` skips the human
    gates (useful for unattended/offline runs)."""
    deps = deps or PipelineDeps()
    orch = (
        Orchestrator.resume(session_id) if session_id else Orchestrator.start(query)
    )

    orch.handlers[Stage.CLARIFY] = _make_clarify_handler(deps)
    orch.handlers[Stage.PLAN] = _make_plan_handler(deps)
    orch.handlers[Stage.SYNTHESIZE] = _synthesize_handler
    orch.handlers[Stage.WRITE] = _make_write_handler(deps)

    orch.phase_handlers[ResearchPhase.ACQUIRE] = _make_acquire_handler(deps)
    orch.phase_handlers[ResearchPhase.EXTRACT] = _make_extract_handler(deps)
    orch.phase_handlers[ResearchPhase.VERIFY] = _make_verify_handler(deps)

    if register_checkpoints:
        orch.post_handlers[Stage.PLAN] = cp1_handler
        orch.post_handlers[Stage.WRITE] = cp2_handler
        orch.phase_handlers[ResearchPhase.MID_ACQUIRE] = cp3_handler

    return orch


def run_pipeline(orch: Orchestrator, *, max_steps: int = 100) -> Stage:
    """Drive a wired orchestrator to DONE, then publish the approved report.

    Catches :class:`CheckpointRejected` (user rejected a plan/draft) and leaves the
    session at its last persisted stage (aborted, resumable). On reaching DONE the
    approved draft is written to ``data/reports/{session_id}.md``."""
    try:
        final = orch.run_to_completion(max_steps=max_steps)
    except CheckpointRejected as exc:
        print(f"run aborted: {exc}")
        return orch.stage

    draft = orch.store.load_draft()
    if draft is not None:
        path = orch.store.write_report(draft)
        print(f"report written: {path}")
    return final
