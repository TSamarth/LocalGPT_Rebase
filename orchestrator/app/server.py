"""
Unified localhost API server (E4.T1) — REST surface + A2A protocol in one process.

Builds the FastAPI ASGI app via ``get_fast_api_app`` over an ``agents_dir`` that
contains the ``localgpt_research`` package (which re-exports the resumable
``App`` from ``app.adk_app``). With ``a2a=True`` the same process serves the REST
surface (``/run_sse``, session CRUD, ``/list-apps``) **and** the A2A protocol
(RPC at ``/a2a/localgpt_research`` + agent card at
``/a2a/localgpt_research/.well-known/agent-card.json``). ``session_service_uri``
is wired to ``config.SESSION_DB_URL`` so DB-backed resume-by-``invocation_id``
works across restarts.

Equivalent CLI form::

    adk api_server --a2a --port 8001 --session_service_uri <db>

Binding is **localhost only** (never ``0.0.0.0``) — local-first, no external
exposure. Building the app is offline-safe: agent discovery + FastAPI
construction never reach Ollama (``LiteLlm`` imports/generates lazily).

ADK 2.3.0 note: ``get_fast_api_app`` reads the static ``agent.json`` card inside
its A2A setup block via ``json.load``, but a later function-local ``import json``
shadows the module for the whole function — so that load raises
``UnboundLocalError`` and the A2A agent is silently skipped (no card, no RPC
route). ``_mount_a2a`` below replicates the intended mount with a DB-backed
runner so the REST and A2A surfaces share the same session DB.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

from app.config import config

APP_NAME = "localgpt_research"
HOST = "localhost"
PORT = 8001

# The agents_dir is the orchestrator directory (parent of this ``app`` package),
# which contains the ``localgpt_research`` package the loader imports by app_name.
_ORCH_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = str(_ORCH_DIR)
_AGENT_CARD_PATH = _ORCH_DIR / APP_NAME / "agent.json"


def build_server() -> FastAPI:
    """Construct the unified REST + A2A FastAPI app bound to the local DB.

    ADK 2.3.0 fix: pass ``a2a=False`` so ADK never enters the buggy A2A setup
    block (function-local ``import json`` at the bottom of ``get_fast_api_app``
    shadows the module-level import, causing ``UnboundLocalError`` when
    ``json.load(f)`` is called inside the A2A loop).  ``_mount_a2a`` below
    performs the correct A2A wiring directly.
    """
    server_app = get_fast_api_app(
        agents_dir=AGENTS_DIR,
        session_service_uri=config.SESSION_DB_URL,
        a2a=False,
        host=HOST,
        port=PORT,
        web=False,
    )
    _mount_a2a(server_app)
    return server_app


def mount_a2a_routes(server_app, *, runner, agent_card, app_name) -> None:
    """Mount the A2A card + RPC routes for *app_name* over *runner*.

    The route-building core shared by the production :func:`_mount_a2a` and the
    offline A2A HITL gate (``tests/test_a2a_hitl_gate.py``) so both exercise the
    SAME a2a-sdk wiring (``A2aAgentExecutor`` → ``DefaultRequestHandler`` →
    ``A2AStarletteApplication``). ``runner`` is passed straight to
    ``A2aAgentExecutor``, which accepts either a ``Runner`` instance or a
    (sync/async) callable that returns one — so production can keep its lazy
    DB-backed loader while the gate injects a stub-workflow runner directly.

    No-op if the card route is already present (e.g. a future ADK that fixes the
    ``json``-shadow bug mounts it itself).
    """
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import (
        InMemoryPushNotificationConfigStore,
        InMemoryTaskStore,
    )
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor

    card_url = f"/a2a/{app_name}{AGENT_CARD_WELL_KNOWN_PATH}"
    if any(getattr(r, "path", None) == card_url for r in server_app.routes):
        return

    request_handler = DefaultRequestHandler(
        agent_executor=A2aAgentExecutor(runner=runner),
        task_store=InMemoryTaskStore(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )
    for route in a2a_app.routes(rpc_url=f"/a2a/{app_name}", agent_card_url=card_url):
        server_app.router.routes.append(route)


def _mount_a2a(server_app: FastAPI) -> None:
    """Mount the A2A card + RPC routes for ``localgpt_research`` (production).

    Works around the ADK 2.3.0 ``json``-shadowing bug (see module docstring) by
    loading the static ``agent.json`` and wiring the A2A executor over a
    DB-backed runner via :func:`mount_a2a_routes`. The runner is built lazily
    (and cached) inside ``_runner_loader`` so the same session DB backs both the
    REST and A2A surfaces and no DB is opened until the first A2A request.
    """
    from a2a.types import AgentCard

    from app.adk_app import app as research_app
    from app.runner import build_runner

    _cache: dict[str, object] = {}

    async def _runner_loader():
        if "runner" not in _cache:
            _cache["runner"] = build_runner(research_app)
        return _cache["runner"]

    if not _AGENT_CARD_PATH.exists():
        raise FileNotFoundError(
            f"A2A agent card not found: {_AGENT_CARD_PATH}. "
            "Create localgpt_research/agent.json before starting the server."
        )
    agent_card = AgentCard(
        **json.loads(_AGENT_CARD_PATH.read_text(encoding="utf-8"))
    )
    mount_a2a_routes(
        server_app,
        runner=_runner_loader,
        agent_card=agent_card,
        app_name=APP_NAME,
    )


def main() -> None:
    """Serve the app under uvicorn, bound to localhost only."""
    import uvicorn

    uvicorn.run(build_server(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
