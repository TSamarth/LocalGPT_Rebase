"""
E5.S2.T3 — Live localhost A2A round-trip test.

Starts app/server.py as a subprocess, drives a full HITL research run over
REST (like app/cli.py) against the real Ollama model, and asserts:
  - A2A agent card at well-known path returns 200 + valid JSON
  - CP1 and CP2 checkpoint pauses observed over SSE
  - Run terminates with a ClaimLedger

Run with:
    uv run pytest tests/test_live_a2a_roundtrip.py -m live -s
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from google.adk.events import Event
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    has_request_input_function_call,
)
from google.genai import types

from app.schemas import ClaimLedger
from app.server import APP_NAME, HOST, PORT

pytestmark = pytest.mark.live

_BASE_URL = f"http://{HOST}:{PORT}"
_AGENT_CARD_PATH = f"/a2a/{APP_NAME}/.well-known/agent-card.json"
_QUERY = "What are the main differences between Rust async runtimes Tokio and async-std?"
_USER_ID = "live-a2a-test-user"
_MAX_HOPS = 30
_SERVER_STARTUP_TIMEOUT = 90  # seconds


# ---------------------------------------------------------------------------
# Ollama + server availability helpers
# ---------------------------------------------------------------------------


def _ollama_available() -> bool:
    """Return True if Ollama is reachable at the configured base URL."""
    from app.config import config

    try:
        r = httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _wait_for_server(base_url: str, card_path: str, timeout: float) -> bool:
    """Poll the agent card URL until the server responds 200 or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}{card_path}", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Module-scoped server subprocess fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server():
    """Start app/server.py as a subprocess; skip if Ollama is not reachable."""
    if not _ollama_available():
        pytest.skip("Ollama not reachable — skipping live A2A round-trip tests")

    orchestrator_dir = Path(__file__).parent.parent
    python = str(orchestrator_dir / ".venv" / "Scripts" / "python.exe")

    proc = subprocess.Popen(
        [python, "-m", "app.server"],
        cwd=str(orchestrator_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    ready = _wait_for_server(_BASE_URL, _AGENT_CARD_PATH, _SERVER_STARTUP_TIMEOUT)
    if not ready:
        proc.terminate()
        proc.wait(timeout=5)
        pytest.fail(
            f"Server subprocess did not become ready within {_SERVER_STARTUP_TIMEOUT}s"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# REST HITL helpers — mirror app/cli.py logic in test form
# ---------------------------------------------------------------------------


async def _create_session(client: httpx.AsyncClient, app_name: str, user_id: str) -> str:
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
):
    """POST to /run_sse and yield each parsed Event."""
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


def _extract_request_input(event: Event) -> tuple[str, Any, str | None] | None:
    """Return (interrupt_id, payload, message) from a RequestInput event, or None."""
    if not has_request_input_function_call(event):
        return None
    for part in event.content.parts:
        fc = getattr(part, "function_call", None)
        if fc and fc.name == "adk_request_input":
            args = fc.args or {}
            return fc.id, args.get("payload"), args.get("message")
    return None


def _auto_reply(interrupt_id: str, payload: Any) -> dict[str, Any]:
    """Auto-approve any checkpoint type.

    * CP3 (interrupt_id starts with ``cp3_``) → done verb (``d``)
    * CP2 (payload is a str draft)             → approve unchanged (empty string)
    * CP1 (payload is a plan dict)             → approve as-is
    """
    if interrupt_id.startswith("cp3_"):
        return {"result": "d"}
    if isinstance(payload, str):
        return {"result": ""}
    return payload if isinstance(payload, dict) else {}


async def _drive_rest_hitl(
    client: httpx.AsyncClient,
    app_name: str,
    user_id: str,
    query: str,
) -> tuple[Any, list[dict], dict]:
    """Drive a full HITL run over REST, auto-approving every checkpoint.

    Returns (final_output, pauses, state).
    """
    session_id = await _create_session(client, app_name, user_id)
    new_message = types.Content(role="user", parts=[types.Part(text=query)])
    invocation_id: str | None = None
    final_output: Any = None
    pauses: list[dict] = []
    state: dict = {}

    for _ in range(_MAX_HOPS):
        pending: tuple[str, dict] | None = None

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
            if event.output is not None:
                final_output = event.output

            extracted = _extract_request_input(event)
            if extracted is not None:
                iid, payload, _message = extracted
                invocation_id = event.invocation_id
                if iid.startswith("cp3_"):
                    cp_type = "CP3"
                elif isinstance(payload, str):
                    cp_type = "CP2"
                else:
                    cp_type = "CP1"
                pauses.append({"interrupt_id": iid, "checkpoint": cp_type})
                pending = (iid, _auto_reply(iid, payload))

        if pending is None:
            break

        new_message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    return final_output, pauses, state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_agent_card_reachable(live_server):
    """A2A agent card must be at the well-known path with valid JSON content."""
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=10.0) as client:
        resp = await client.get(_AGENT_CARD_PATH)

    assert resp.status_code == 200, (
        f"Agent card at {_AGENT_CARD_PATH} returned {resp.status_code}"
    )
    card = resp.json()
    assert card.get("name"), "Agent card missing 'name'"
    assert card.get("url"), "Agent card missing 'url'"

    print(f"\n[PASS] A2A agent card at {_AGENT_CARD_PATH} — 200 OK (name={card['name']!r})")


@pytest.mark.asyncio
async def test_rest_hitl_full_roundtrip(live_server):
    """Full HITL round-trip over REST: CP1 + CP2 observed, run terminates with ClaimLedger."""
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
    ) as client:
        final_output, pauses, state = await _drive_rest_hitl(
            client, APP_NAME, _USER_ID, _QUERY
        )

    cp_types = [p["checkpoint"] for p in pauses]
    saw_cp1 = "CP1" in cp_types
    saw_cp2 = "CP2" in cp_types

    # Collect invocation IDs for evidence output
    cp1_entry = next((p for p in pauses if p["checkpoint"] == "CP1"), None)
    cp2_entry = next((p for p in pauses if p["checkpoint"] == "CP2"), None)

    print(f"\n[{'PASS' if saw_cp1 else 'FAIL'}] CP1 pause observed"
          + (f" (interrupt_id: {cp1_entry['interrupt_id']})" if cp1_entry else ""))
    print(f"[{'PASS' if saw_cp2 else 'FAIL'}] CP2 pause observed"
          + (f" (interrupt_id: {cp2_entry['interrupt_id']})" if cp2_entry else ""))

    assert saw_cp1, f"CP1 checkpoint not observed in pause sequence: {cp_types}"
    assert saw_cp2, f"CP2 checkpoint not observed in pause sequence: {cp_types}"

    # Verify terminal output is a valid ClaimLedger
    assert final_output is not None, "Workflow produced no terminal output (None)"
    ledger = ClaimLedger.model_validate(final_output)

    print(f"[PASS] Run completed with ClaimLedger ({len(ledger.claims)} claims)")
