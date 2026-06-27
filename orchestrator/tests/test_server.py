"""E4.T1 gate — unified localhost server exposes REST + A2A offline.

Locks the T1 gate as a regression test: building ``app.server.build_server``
must be offline-safe (no Ollama), surface ``localgpt_research`` via REST
``/list-apps``, mount the REST ``/run_sse`` route, and publish the A2A agent
card at the well-known path. Driven in-process over ``httpx.ASGITransport`` so
no real port is bound.
"""
from __future__ import annotations

import httpx
import pytest

from app.server import build_server


@pytest.fixture
def server_app():
    return build_server()


def test_run_sse_route_present(server_app):
    paths = {getattr(r, "path", None) for r in server_app.routes}
    assert "/run_sse" in paths


async def test_list_apps_includes_localgpt_research(server_app):
    transport = httpx.ASGITransport(app=server_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        resp = await client.get("/list-apps")
    assert resp.status_code == 200
    assert "localgpt_research" in resp.json()


async def test_a2a_agent_card_published(server_app):
    transport = httpx.ASGITransport(app=server_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        resp = await client.get(
            "/a2a/localgpt_research/.well-known/agent-card.json"
        )
    assert resp.status_code == 200
    card = resp.json()
    assert card.get("name")
    assert card.get("url")
    assert card.get("skills")


# The A2A extension URI ADK 2.3.0 uses to signal the new (use_legacy=False)
# A2aAgentExecutor — verified against the installed source
# (google.adk.a2a.agent.interceptors.new_integration_extension) and the adk-docs
# A2A-extension page.
_A2A_NEW_EXECUTOR_EXTENSION = "https://google.github.io/adk-docs/a2a/a2a-extension/"


async def test_a2a_card_content_modes_and_extension(server_app):
    """E4.T2 — served card locks input/output modes, a skill, and the A2A extension.

    The pipeline returns a markdown draft, so ``defaultOutputModes`` must be
    ``text/markdown``; ``defaultInputModes`` stays ``text/plain``. The new-executor
    A2A extension must be advertised under ``capabilities.extensions`` so
    ``use_legacy=False`` clients get routed to the new ``A2aAgentExecutor``.
    """
    transport = httpx.ASGITransport(app=server_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        resp = await client.get(
            "/a2a/localgpt_research/.well-known/agent-card.json"
        )
    assert resp.status_code == 200
    card = resp.json()

    assert card["defaultInputModes"] == ["text/plain"]
    assert card["defaultOutputModes"] == ["text/markdown"]
    assert len(card.get("skills", [])) >= 1

    extension_uris = {
        ext.get("uri") for ext in card.get("capabilities", {}).get("extensions", [])
    }
    assert _A2A_NEW_EXECUTOR_EXTENSION in extension_uris
