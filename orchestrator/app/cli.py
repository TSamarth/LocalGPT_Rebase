"""
Thin REST CLI over the unified server's ``/run_sse`` surface (E4.T3).

Drives a full research run against the localhost server entirely over HTTP — the
v2 analog of ``main.py``'s in-process console loop, over the wire:

1. Create a session (``POST /apps/{app}/users/{user}/sessions``).
2. ``POST /run_sse`` with the query; parse the SSE event stream.
3. When the workflow pauses at a CP1/CP2/CP3 ``RequestInput`` (surfaced as the
   long-running ``adk_request_input`` function call), render the checkpoint to
   the terminal and collect the human reply — reusing the SAME display + verb
   logic as the in-process path: ``app.checkpoint.cp1_checkpoint`` /
   ``cp2_checkpoint`` for CP1/CP2 (approve / edit / reject), and
   ``app.cp3_adapter.apply_cp3_verbs`` for the CP3 ``+add/-exclude/r/d`` grammar.
4. Post the ``RequestInput`` response and RESUME by ``invocation_id`` on the next
   ``/run_sse`` POST (ADK's resume-by-``invocation_id`` mechanic).
5. Render the terminal result (the workflow ``event.output`` ledger + the
   approved ``v2_draft``), both read off the SSE stream.

The SSE payloads are ``Event.model_dump_json(by_alias=True, exclude_none=True)``,
so each ``data:`` line round-trips through ``Event.model_validate_json`` and the
official ADK HITL helpers (``has_request_input_function_call`` /
``create_request_input_response``) detect + answer the pause — exactly as the
in-process resume harness does. Target is localhost only (``app.server`` HOST/PORT).
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

import httpx
from google.adk.events import Event
from google.adk.workflow.utils._workflow_hitl_utils import (
    REQUEST_INPUT_FUNCTION_CALL_NAME,
    create_request_input_response,
    has_request_input_function_call,
)
from google.genai import types

from app import checkpoint
from app.checkpoint import CheckpointRejected
from app.cp3_adapter import apply_cp3_verbs
from app.schemas import ClaimLedger, ResearchPlan, ScoredURL
from app.server import APP_NAME, HOST, PORT
from app.workflow import CHECKPOINT_REJECT

USER_ID = "cli-user"
BASE_URL = f"http://{HOST}:{PORT}"

# Guard against a non-terminating resume loop (real runs need far fewer hops).
_MAX_HOPS = 200


async def _create_session(client: httpx.AsyncClient, app_name: str, user_id: str) -> str:
    """Create a session over REST and return its id."""
    resp = await client.post(f"/apps/{app_name}/users/{user_id}/sessions", json={})
    resp.raise_for_status()
    return resp.json()["id"]


async def _stream_run(
    client: httpx.AsyncClient,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    new_message: types.Content,
    invocation_id: str | None,
) -> AsyncIterator[Event]:
    """POST to ``/run_sse`` and yield each streamed event, reconstructed from JSON.

    ``invocation_id`` is sent only on resume POSTs; ADK auto-resolves the paused
    invocation and skips the already-completed nodes.
    """
    body: dict[str, Any] = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": new_message.model_dump(mode="json", by_alias=True, exclude_none=True),
        "streaming": False,
    }
    if invocation_id:
        body["invocation_id"] = invocation_id
    async with client.stream("POST", "/run_sse", json=body) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                yield Event.model_validate_json(line[len("data:"):].strip())


def _request_input_call(event: Event) -> tuple[str, Any, str | None]:
    """Extract ``(interrupt_id, payload, message)`` from a RequestInput event."""
    for part in event.content.parts:
        fc = part.function_call
        if fc and fc.name == REQUEST_INPUT_FUNCTION_CALL_NAME:
            args = fc.args or {}
            return fc.id, args.get("payload"), args.get("message")
    raise ValueError("event carries no adk_request_input function call")


def _answer_cp1(payload: Any) -> dict[str, Any]:
    """Render CP1 and return the resume reply (an approved/edited plan dict, or
    the schema-valid empty-``subtopics`` reject signal)."""
    plan = ResearchPlan.model_validate(payload)
    try:
        approved = checkpoint.cp1_checkpoint(plan)
    except CheckpointRejected:
        return {"subtopics": []}
    return approved.model_dump(mode="json")


def _answer_cp2(payload: Any) -> dict[str, Any]:
    """Render CP2 and return the resume reply: ``{"result": ...}`` — empty to
    approve unchanged, the edited markdown to edit, or the reject sentinel."""
    draft = payload if isinstance(payload, str) else ""
    try:
        approved = checkpoint.cp2_checkpoint(draft)
    except CheckpointRejected:
        return {"result": CHECKPOINT_REJECT}
    return {"result": "" if approved == draft else approved}


def _answer_cp3(payload: Any, message: str | None) -> dict[str, Any]:
    """Render the CP3 candidate list, collect verb lines, and return the raw verb
    reply (``{"result": <verbs>}``) — the server feeds it to ``apply_cp3_verbs``.

    The same ``apply_cp3_verbs`` grammar is run locally only to echo the resulting
    list back as a confirmation; the authoritative apply happens server-side.
    """
    urls = [ScoredURL.model_validate(u) for u in (payload or [])]
    if message:
        print(message)
    lines: list[str] = []
    while True:
        line = input("> ").strip()
        if not line:
            continue
        lines.append(line)
        if line.split(" ", 1)[0].lower() in ("d", "done"):
            break
    reply = "\n".join(lines)
    filtered, supplemental = apply_cp3_verbs(urls, reply)
    print(
        f"({len(filtered)} sources after edits"
        + (", re-acquiring added targets" if supplemental else "")
        + ")"
    )
    return {"result": reply}


def _answer_checkpoint(interrupt_id: str, payload: Any, message: str | None) -> dict[str, Any]:
    """Dispatch a pause to the matching CP renderer by interrupt-id / payload shape.

    CP3 ids are ``cp3_*``; CP2 carries a ``str`` draft payload; CP1 carries the
    plan dict — robust to reject re-pauses (no positional bookkeeping needed).
    """
    if interrupt_id.startswith("cp3_"):
        return _answer_cp3(payload, message)
    if isinstance(payload, str):
        return _answer_cp2(payload)
    return _answer_cp1(payload)


def _render_result(final_output: Any, state: dict[str, Any]) -> None:
    """Print the terminal draft + a ledger summary."""
    print("\n=== Result ===")
    draft = state.get("v2_draft")
    if draft:
        print(draft)
    if final_output is not None:
        ledger = ClaimLedger.model_validate(final_output)
        print(f"\n(ledger: {len(ledger.claims)} claims)")


async def run_cli(
    query: str,
    *,
    client: httpx.AsyncClient,
    app_name: str = APP_NAME,
    user_id: str = USER_ID,
) -> tuple[Any, dict[str, Any]]:
    """Drive a full run over REST, returning ``(terminal_output, accumulated_state)``.

    Accepts an injected ``httpx.AsyncClient`` so tests can route the whole
    round-trip through an ``ASGITransport`` (no real port needed).
    """
    session_id = await _create_session(client, app_name, user_id)
    print(f"session: {session_id}")

    new_message = types.Content(role="user", parts=[types.Part(text=query)])
    invocation_id: str | None = None
    final_output: Any = None
    state: dict[str, Any] = {}

    for _ in range(_MAX_HOPS):
        pending: tuple[str, dict[str, Any]] | None = None
        async for event in _stream_run(
            client,
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
            invocation_id=invocation_id,
        ):
            if event.actions and event.actions.state_delta:
                state.update(event.actions.state_delta)
            if has_request_input_function_call(event):
                invocation_id = event.invocation_id
                iid, payload, message = _request_input_call(event)
                pending = (iid, _answer_checkpoint(iid, payload, message))
            if event.output is not None:
                final_output = event.output
        if pending is None:
            break  # no further pause → the run reached a terminal state
        new_message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    _render_result(final_output, state)
    return final_output, state


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    query = " ".join(argv).strip()
    if not query:
        print('usage: python -m app.cli "your research query"')
        return 1

    async def _go() -> None:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=None) as client:
            await run_cli(query, client=client)

    asyncio.run(_go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
