"""
Clarifier agent (Story 3.2, FR1) — the first reasoning stage.

Role: read the *raw* user query and decide whether the run can start. It detects
(a) **ambiguous scope** and (b) **missing constraints** (timeframe / region /
version / audience / depth / …). When the query is underspecified it returns
``status="needs_input"`` with concrete ``questions`` the orchestrator surfaces to
the user **before run start only** (architecture.md §3.2). Otherwise it returns
``status="clear"`` with a tidied ``normalized_query`` for the Planner.

It is **reasoning-only**: built on the shared one-hot model via
``app.llm.build_agent`` with ``output_schema=ClarifyResult`` and **no tools**
(ADK forbids combining a forced output schema with tools).

Design for testability — model invocation is *injectable*:

* :func:`build_clarifier` constructs the ADK ``LlmAgent`` (offline; safe in unit
  tests — ``LiteLlm`` imports ``litellm`` lazily on first generate).
* :func:`parse_clarify_result` is a pure function turning a raw model JSON string
  into a validated :class:`ClarifyResult` (the unit-testable core).
* :func:`clarify` is the thin async entry the orchestrator/tests call. It accepts
  an injectable ``runner`` so tests run **without Ollama**; only when no runner is
  supplied does it spin up a real ADK ``InMemoryRunner``.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from google.adk.agents import LlmAgent

from ..jsonio import invoke_json_with_retry
from ..llm import DETERMINISTIC_CONFIG, build_agent
from ..schemas import ClarifyResult

# Stable identifiers so the orchestrator and session state agree on names.
AGENT_NAME = "clarifier"
OUTPUT_KEY = "clarify_result"

VALID_STATUSES = ("clear", "needs_input")

# ── Role / system prompt ──────────────────────────────────────────────────────
# Kept verbose on purpose: a small local model needs explicit, exhaustive
# instructions and a strict output contract to stay on-rails (FR1).
ROLE_PROMPT = """\
You are the CLARIFIER, the first stage of a multi-agent deep-research pipeline.

Your only job is to inspect the user's RAW research query and decide whether it
is specific enough to research, or whether it must be clarified with the user
FIRST. You do not perform research, browse the web, or answer the question.

Analyse the query for two failure modes:

1. AMBIGUOUS SCOPE — the subject, intent, or boundary of the query is unclear or
   could reasonably be read several different ways. Examples: undefined acronyms,
   vague nouns ("the framework", "this", "it"), multiple unrelated topics fused
   into one request, or an unclear deliverable ("tell me about X").

2. MISSING CONSTRAINTS — material parameters a good report needs are absent.
   Consider, where relevant to the topic:
     - timeframe / recency (e.g. "as of when?", "latest" vs historical)
     - region / jurisdiction / locale
     - version / edition / release of a product, library, or standard
     - audience / depth / level (beginner vs expert, exec vs engineer)
     - comparison baseline or specific entities to compare
     - units, scope of comparison, or success criteria

Decision rule:
  - If EITHER ambiguous scope OR a materially missing constraint is present, set
    status = "needs_input" and produce a short list of concrete, answerable
    QUESTIONS (one per gap, ideally 1-4 total). Do NOT invent answers; ask.
  - If the query is already specific and self-contained, set status = "clear"
    and leave questions empty.

ALWAYS produce a normalized_query: a cleaned, well-formed restatement of the
user's intent in a single sentence — fix grammar/typos, expand obvious
abbreviations, and remove filler. When status is "clear" this is what the Planner
will consume. When status is "needs_input" it is your best-effort interpretation
SO FAR (the missing pieces stay unfilled — never fabricate constraints the user
did not give).

Output ONLY a JSON object matching this exact schema — no prose, no markdown,
no code fences:
{
  "status": "clear" | "needs_input",
  "normalized_query": "<one-sentence cleaned restatement>",
  "questions": ["<question>", ...]   // empty list when status == "clear"
}

Rules:
- questions MUST be empty when status == "clear".
- questions MUST be non-empty when status == "needs_input".
- Keep questions specific and answerable in one line each; never ask the user to
  do the research for you.
"""


def build_clarifier(*, model: Optional[object] = None) -> LlmAgent:
    """Construct the Clarifier ``LlmAgent`` on the shared one-hot model.

    Reasoning-only: passes ``output_schema=ClarifyResult`` and no tools, so ADK
    forces structured JSON output written to session state under ``OUTPUT_KEY``.
    ``model`` is injectable purely to let tests pass a fake ``LiteLlm``; in
    production it is left ``None`` and :func:`app.llm.build_model` supplies the
    shared resident model.
    """
    return build_agent(
        name=AGENT_NAME,
        role_prompt=ROLE_PROMPT,
        output_schema=ClarifyResult,
        output_key=OUTPUT_KEY,
        model=model,  # type: ignore[arg-type]  # LiteLlm | None; only set by tests
        generate_content_config=DETERMINISTIC_CONFIG,
    )


def parse_clarify_result(raw: str | dict | ClarifyResult) -> ClarifyResult:
    """Coerce a raw model output into a validated :class:`ClarifyResult`.

    Accepts a JSON string (possibly fenced / surrounded by stray prose — small
    local models leak both), a pre-parsed dict, or an already-built
    ``ClarifyResult`` (idempotent). Normalises the ``status`` field and enforces
    the FR1 invariant linking ``status`` to ``questions``.

    Raises:
        ValueError: if the text is not valid JSON, is not a JSON object, or
            carries a ``status`` outside the allowed set.
    """
    if isinstance(raw, ClarifyResult):
        return _enforce_invariants(raw)

    if isinstance(raw, dict):
        data = raw
    else:
        data = _loads_lenient(raw)

    if not isinstance(data, dict):
        raise ValueError(
            f"clarifier output must be a JSON object, got {type(data).__name__}"
        )

    status = str(data.get("status", "")).strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(
            f"clarifier status must be one of {VALID_STATUSES!r}, got {status!r}"
        )

    result = ClarifyResult(
        status=status,
        normalized_query=str(data.get("normalized_query", "")).strip(),
        questions=[str(q).strip() for q in (data.get("questions") or []) if str(q).strip()],
    )
    return _enforce_invariants(result)


def _enforce_invariants(result: ClarifyResult) -> ClarifyResult:
    """Apply the FR1 status↔questions contract, repairing trivial mismatches.

    - ``clear``       → questions are irrelevant; drop them.
    - ``needs_input`` → must carry at least one question; an empty list is an
      invalid verdict and is rejected (the model contradicted itself).
    """
    if result.status == "clear":
        if result.questions:
            result = result.model_copy(update={"questions": []})
        return result

    # needs_input
    if not result.questions:
        raise ValueError(
            "clarifier returned status='needs_input' with no questions — "
            "ambiguity reported but nothing to ask"
        )
    return result


def _loads_lenient(raw: str) -> object:
    """``json.loads`` with a fallback that strips ```` ``` ```` fences / stray
    prose by extracting the outermost ``{...}`` span. Keeps strict parsing first
    so well-formed output is never mangled."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"clarifier output is not valid JSON: {exc}") from exc

    raise ValueError("clarifier output is not valid JSON and contains no JSON object")


# Type of an injectable model caller: (query) -> raw JSON string (sync or async).
RunnerFn = Callable[[str], "str | Awaitable[str]"]


async def clarify(query: str, *, runner: Optional[RunnerFn] = None) -> ClarifyResult:
    """Run the Clarifier over ``query`` and return a validated ``ClarifyResult``.

    The model call is injectable. Pass ``runner`` — any callable mapping the raw
    query to the model's raw JSON string (sync or async) — to run **without**
    Ollama (unit tests do exactly this). When ``runner`` is ``None`` a real ADK
    ``InMemoryRunner`` over :func:`build_clarifier` is used (live path).

    Args:
        query: the raw user query.
        runner: optional injected model caller for offline/testing.

    Returns:
        The parsed, invariant-checked :class:`ClarifyResult`.
    """
    if not query or not query.strip():
        raise ValueError("clarify() requires a non-empty query")

    if runner is not None:
        raw = runner(query)
        if isinstance(raw, Awaitable):
            raw = await raw
        return parse_clarify_result(raw)

    return await _run_live(query)


async def _run_live(query: str) -> ClarifyResult:
    """Live path: drive the Clarifier agent through an ADK ``InMemoryRunner`` and
    read the structured ``ClarifyResult`` ADK writes to session state under
    ``OUTPUT_KEY``. Imported lazily so the offline/test path never needs a
    running Ollama or the heavier runner machinery.

    Wrapped in :func:`invoke_json_with_retry` — a small local model occasionally
    truncates or garbles the JSON it must emit; one bounded corrective re-ask
    (:data:`app.jsonio.JSON_ONLY_REASK`) turns that into a self-healing retry
    instead of a hard crash.
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from ..config import config

    agent = build_clarifier()
    runner = InMemoryRunner(agent=agent, app_name=config.APP_NAME)

    user_id = "clarifier-user"
    session = await runner.session_service.create_session(
        app_name=config.APP_NAME, user_id=user_id
    )

    async def _call(prompt: str) -> str:
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        async for _ in runner.run_async(
            user_id=user_id, session_id=session.id, new_message=message
        ):
            pass  # drain events; the agent writes its output to session state

        state = (
            await runner.session_service.get_session(
                app_name=config.APP_NAME, user_id=user_id, session_id=session.id
            )
        ).state
        raw = state.get(OUTPUT_KEY)
        if raw is None:
            raise RuntimeError(
                f"clarifier produced no output under state key {OUTPUT_KEY!r}"
            )
        return raw

    raw = await invoke_json_with_retry(_call, query)
    return parse_clarify_result(raw)
