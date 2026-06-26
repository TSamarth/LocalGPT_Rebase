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
