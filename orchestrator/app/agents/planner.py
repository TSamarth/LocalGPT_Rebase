"""
Planner agent (Story 3.3, FR2) — turns a normalized query into a ``ResearchPlan``.

The Planner is a **reasoning-only** specialist on the shared one-hot model
(``app.llm.build_agent`` with ``output_schema=ResearchPlan`` and no tools). It
decomposes the query into focused *subtopics*, picks an adaptive ``depth``
(shallow / normal / deep) from the query's complexity and breadth, and proposes
``source_classes`` per subtopic. The orchestrator presents the result at
Checkpoint 1 (architecture.md §3.3).

Two responsibilities are split on purpose:

* The **model** owns the *judgement* — how many subtopics, what questions, which
  source classes, and which ``depth`` the query warrants.
* A **deterministic** post-step (:func:`apply_depth_targets`) owns the *policy* —
  mapping the chosen ``depth`` onto every subtopic's ``target_evidence`` using the
  ``config.TARGET_EVIDENCE_*`` knobs. Keeping this out of the prompt means the
  stop-rule (architecture.md §4) is driven by config, never by an LLM guess, and
  stays trivially testable without a running model.

Model invocation is injectable: :func:`plan` takes a ``runner`` callable so unit
tests exercise the full parse → normalise path against canned JSON, with no
Ollama and no network. The default runner drives the real agent through ADK's
``Runner`` over an in-memory session.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional, Union

from google.adk.agents import LlmAgent

from ..config import config
from ..jsonio import invoke_json_with_retry
from ..llm import DETERMINISTIC_CONFIG, build_agent
from ..schemas import Depth, ResearchPlan

# A runner takes the normalized query and returns the model's structured output,
# either as a raw JSON string or an already-parsed mapping. Async so the default
# (ADK Runner over Ollama) and test doubles share one signature.
PlannerRunner = Callable[[str], Awaitable[Union[str, dict]]]

#: state/output key the agent writes its ``ResearchPlan`` JSON under.
OUTPUT_KEY = "research_plan"

#: Agent name (also used as the ADK app name for the default runner).
AGENT_NAME = "planner"

ROLE_PROMPT = """\
You are the Planner in a local deep-research pipeline. You receive ONE normalized
research query and produce a structured research plan. You do not browse, crawl,
or invent facts — you only decompose the query and decide how hard to dig.

Return ONLY a JSON object that conforms to this schema (no prose, no markdown):

{
  "subtopics": [
    {
      "id": "s1",
      "question": "a focused sub-question that advances the main query",
      "target_evidence": 3,
      "source_classes": ["web" | "academic" | "code" | "seed", ...]
    }
  ],
  "depth": "shallow" | "normal" | "deep",
  "seed_urls": [],
  "est_iterations": 1
}

Rules:
- Decompose the query into 2–6 NON-OVERLAPPING subtopics. Each must be independently
  researchable and together they must cover the query. Fewer, sharper subtopics beat
  many vague ones. Give each a short stable id ("s1", "s2", ...).
- Choose `depth` ADAPTIVELY from the query's complexity and breadth:
    * "shallow" — a single narrow factual question; a quick answer suffices.
    * "normal"  — a moderate topic needing a few angles and some corroboration.
    * "deep"    — broad, contested, fast-moving, technical, or research-grade topics
      that demand many sources, academic literature, or citation tracing.
- Set `source_classes` per subtopic from where the evidence should come:
    * "web" for general/current information (the usual default),
    * "academic" for scientific, medical, or research-grade claims,
    * "code" for API/library/implementation questions,
    * "seed" only when the user supplied specific URLs/files.
  Prefer "academic" generously when `depth` is "deep".
- Set `target_evidence` to a small positive integer (>= 1) reflecting how much
  corroboration each subtopic needs; it will be normalized downstream, so a sensible
  placeholder scaled to depth (about 2 for shallow, 3 for normal, 5 for deep) is fine.
- Leave `seed_urls` empty unless the query explicitly names URLs to include.
- Set `est_iterations` to a small integer (1–3) estimating research passes needed.

Output the JSON object and nothing else.
"""


def build_planner(*, model: Optional[object] = None) -> LlmAgent:
    """Construct the reasoning-only Planner agent.

    Wires the shared model factory with ``output_schema=ResearchPlan`` so ADK
    forces structured JSON, no tools (Planner never crawls — architecture.md
    §3.3, least-privilege §1). ``model`` is injectable for tests that want to
    avoid even constructing a ``LiteLlm``.
    """
    return build_agent(
        name=AGENT_NAME,
        role_prompt=ROLE_PROMPT,
        output_schema=ResearchPlan,
        output_key=OUTPUT_KEY,
        model=model,
        generate_content_config=DETERMINISTIC_CONFIG,
    )


def _target_for_depth(depth: Depth) -> int:
    """Map a research ``depth`` to its configured corroboration target."""
    return {
        Depth.SHALLOW: config.TARGET_EVIDENCE_SHALLOW,
        Depth.NORMAL: config.TARGET_EVIDENCE_NORMAL,
        Depth.DEEP: config.TARGET_EVIDENCE_DEEP,
    }[depth]


def apply_depth_targets(plan: ResearchPlan) -> ResearchPlan:
    """Normalize every subtopic's ``target_evidence`` to the plan's depth.

    Deterministic policy step (no model): overwrites whatever the LLM proposed
    with ``config.TARGET_EVIDENCE_{SHALLOW,NORMAL,DEEP}`` so the stop-rule is
    config-driven and reproducible. Returns a NEW ``ResearchPlan`` (the input is
    left untouched) to keep this a pure function.
    """
    target = _target_for_depth(plan.depth)
    updated = plan.model_copy(deep=True)
    for subtopic in updated.subtopics:
        subtopic.target_evidence = target
    return updated


def parse_plan(raw: Union[str, dict, ResearchPlan]) -> ResearchPlan:
    """Validate raw model output into a ``ResearchPlan`` and normalize targets.

    Accepts a JSON string, a mapping, or an already-built ``ResearchPlan`` (so
    callers can hand back whatever the runner produced). Always runs
    :func:`apply_depth_targets` so the returned plan's ``target_evidence`` values
    are guaranteed consistent with its ``depth``.
    """
    if isinstance(raw, ResearchPlan):
        validated = raw
    elif isinstance(raw, str):
        validated = ResearchPlan.model_validate_json(raw)
    else:
        validated = ResearchPlan.model_validate(raw)
    return apply_depth_targets(validated)


async def _default_runner(normalized_query: str) -> str:
    """Drive the real Planner agent through ADK over the local Ollama model.

    Built lazily and only used when no ``runner`` is injected, so importing this
    module (and unit-testing it) never requires a running Ollama. Returns the raw
    JSON text the agent emitted under ``OUTPUT_KEY``.

    Wrapped in :func:`invoke_json_with_retry` — a small local model occasionally
    truncates or garbles the JSON it must emit; one bounded corrective re-ask
    (:data:`app.jsonio.JSON_ONLY_REASK`) turns that into a self-healing retry
    instead of a hard crash.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = build_planner()
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=AGENT_NAME, user_id="orchestrator", session_id="plan"
    )
    runner = Runner(agent=agent, app_name=AGENT_NAME, session_service=session_service)

    async def _call(prompt: str) -> str:
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        final_text = ""
        for event in runner.run(
            user_id="orchestrator", session_id="plan", new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(p.text or "" for p in event.content.parts)

        # ADK may return the structured object under output_key in session state;
        # fall back to that if the final text wasn't plain JSON.
        session = await session_service.get_session(
            app_name=AGENT_NAME, user_id="orchestrator", session_id="plan"
        )
        if session is not None:
            stored = session.state.get(OUTPUT_KEY)
            if isinstance(stored, dict):
                return json.dumps(stored)
            if isinstance(stored, str) and stored.strip():
                return stored
        return final_text

    return await invoke_json_with_retry(_call, normalized_query)


async def plan(normalized_query: str, *, runner: Optional[PlannerRunner] = None) -> ResearchPlan:
    """Produce a normalized ``ResearchPlan`` for a normalized query.

    The model decides subtopics + adaptive ``depth``; this function then enforces
    the config-driven ``target_evidence`` policy via :func:`apply_depth_targets`.

    ``runner`` is injectable: pass an async callable ``(query) -> str | dict`` to
    run offline against canned output (unit tests, replay). When omitted, the
    default runner drives the real agent through ADK + Ollama (self-healing on
    bad JSON via :func:`_default_runner`).
    """
    invoke = runner or _default_runner
    raw = await invoke(normalized_query)
    return parse_plan(raw)
