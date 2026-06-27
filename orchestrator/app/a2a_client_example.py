"""E4.T4 — RemoteA2aAgent consumption example (documentation/example only).

Shows how a *client* wires up :class:`RemoteA2aAgent` against the running
unified localhost server (``app/server.py``) and consumes the whole
``localgpt_research`` pipeline as a single local sub-agent over the A2A protocol.

This is a SEAM/EXAMPLE only — it deliberately does **not** split the pipeline's
specialists (clarifier/planner/acquirer/…) onto their own A2A servers. The whole
resumable ``Workflow`` is exposed by one server and consumed here as one remote
agent (per the E4 epic note).

Why ``use_legacy=False``
------------------------
Setting ``use_legacy=False`` activates the new ADK↔A2A integration: the client
advertises the A2A extension so the server runs the new ``A2aAgentExecutor``.
That executor is what surfaces our ``RequestInput`` HITL checkpoints (CP1 plan /
CP2 draft / CP3 sources) as A2A ``input-required`` tasks the client answers to
resume — the round-trip proven by the T1b hard gate
(``tests/test_a2a_hitl_gate.py``).

Offline-safe to import
----------------------
Importing this module does no network I/O and reaches no model — the card URL is
just a string and ``RemoteA2aAgent`` resolves the card lazily on first use. The
offline test (``tests/test_a2a_client_example.py``) drives the agent built here
in-process via an ``httpx.ASGITransport`` against the DB-backed stub server,
reusing the T1b harness — no real port or model is bound.

Usage against a live server::

    # Terminal 1: start the unified server (REST + A2A) on localhost:8001
    python -m app.server

    # Terminal 2: consume it over A2A
    python -m app.a2a_client_example "What changed in Python packaging in 2024?"
"""
from __future__ import annotations

import sys
from typing import Optional

import httpx
from google.adk.agents.remote_a2a_agent import (
    AGENT_CARD_WELL_KNOWN_PATH,
    RemoteA2aAgent,
)

from app.server import APP_NAME, HOST, PORT

# Name for the remote handle on the client side (must be a valid identifier).
REMOTE_AGENT_NAME = "localgpt_research_remote"

# The well-known agent-card URL the unified server publishes. This mirrors the
# card route the server mounts at ``f"/a2a/{APP_NAME}{AGENT_CARD_WELL_KNOWN_PATH}"``
# (see ``app/server.py``); ``AGENT_CARD_WELL_KNOWN_PATH`` is ``a2a-sdk``'s
# ``/.well-known/agent-card.json``.
AGENT_CARD_URL = f"http://{HOST}:{PORT}/a2a/{APP_NAME}{AGENT_CARD_WELL_KNOWN_PATH}"


def build_remote_research_agent(
    *,
    httpx_client: Optional[httpx.AsyncClient] = None,
) -> RemoteA2aAgent:
    """Construct a :class:`RemoteA2aAgent` consuming the pipeline over A2A.

    Drop the returned agent into a parent's ``sub_agents=[...]`` (to delegate to
    it from an LLM root agent) or make it the ``root_agent`` of a client ``App``
    and drive it with a ``Runner`` (the offline test does the latter).

    Args:
        httpx_client: Optional shared async client. Production usage omits this
            and lets ``RemoteA2aAgent`` create its own client against the live
            server. The offline test injects an ``httpx.ASGITransport`` client so
            the whole A2A round-trip runs in-process with no real port bound.
    """
    return RemoteA2aAgent(
        name=REMOTE_AGENT_NAME,
        description="Local-first deep-research pipeline consumed over the A2A protocol.",
        agent_card=AGENT_CARD_URL,
        httpx_client=httpx_client,
        # New ADK↔A2A integration → HITL RequestInput surfaces as input-required.
        use_legacy=False,
    )


async def consume(query: str) -> None:
    """Drive one run against a LIVE server, streaming events to stdout.

    Demonstrates the client side: wrap the remote agent in a resumable client
    ``App``/``Runner`` and stream events. ``input-required`` checkpoints (CP1/CP2)
    are surfaced here as a notice — answering them to resume is exactly what the
    T1b gate automates; a real interactive client would collect the reply and
    re-send it on the same A2A task (see ``app/cli.py`` for the REST analog).

    Imported lazily-friendly: nothing here runs unless this coroutine is awaited.
    """
    from google.adk.apps import App, ResumabilityConfig
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.workflow.utils._workflow_hitl_utils import (
        has_request_input_function_call,
    )
    from google.genai import types

    remote = build_remote_research_agent()
    runner = Runner(
        app=App(
            name="a2a_client",
            root_agent=remote,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=InMemorySessionService(),
    )
    session = await runner.session_service.create_session(
        app_name="a2a_client", user_id="example-user"
    )
    message = types.Content(role="user", parts=[types.Part(text=query)])
    async for event in runner.run_async(
        user_id="example-user", session_id=session.id, new_message=message
    ):
        if has_request_input_function_call(event):
            print("[checkpoint] server requested input (answer to resume).")
        elif event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="")
    print()


def main() -> None:
    import asyncio

    query = sys.argv[1] if len(sys.argv) > 1 else "Summarize recent advances in retrieval-augmented generation."
    asyncio.run(consume(query))


if __name__ == "__main__":
    main()
