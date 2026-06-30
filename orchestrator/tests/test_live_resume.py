"""
E5.S2.T2 — Live resume-by-invocation_id test.

Proves that a research run paused at CP1 can be resumed in a new runner
instance (simulating a process restart) using the persisted
DatabaseSessionService.

Run with:
    uv run pytest tests/test_live_resume.py -m live -s
"""
from __future__ import annotations

import warnings
from typing import Any

import pytest
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.workflow import node
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    has_request_input_function_call,
)
from google.genai import types

from app.config import config
from app.schemas import (
    ClarifyResult,
    ClaimLedger,
    Depth,
    ResearchPlan,
    Subtopic,
)
from app.workflow import build_research_workflow
from tests.eval.fixtures.seeded_sources import make_generic_stubs

# Suppress the ResumabilityConfig experimental warning — same filter used in
# test_live_acceptance.py and offline tests.
warnings.filterwarnings(
    "ignore",
    message=r".*ResumabilityConfig.*",
    category=UserWarning,
)

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RESUME_QUERY = "What are the main differences between Rust async runtimes Tokio and async-std?"
_USER_ID = "live-resume-test-user"
_APP_NAME = "live_resume_test"
_MAX_HOPS = 50


# ---------------------------------------------------------------------------
# Ollama availability guard
# ---------------------------------------------------------------------------


def ollama_available() -> bool:
    """Return True if Ollama is reachable at the configured base URL."""
    import httpx

    try:
        r = httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def skip_if_no_ollama():
    if not ollama_available():
        pytest.skip("Ollama not reachable — skipping live tests")


# ---------------------------------------------------------------------------
# Stub nodes
# ---------------------------------------------------------------------------


def _clarifier_stub_clear() -> object:
    """Clarifier stub: always returns clear/normalized — no pre-CP1 pause."""

    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(
            status="clear",
            normalized_query="What are the main differences between Tokio and async-std?",
        )

    return _stub


def _planner_stub_shallow() -> object:
    """Planner stub: single subtopic at SHALLOW depth so the loop runs one pass."""

    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[Subtopic(id="s1", question=node_input, target_evidence=1)],
            depth=Depth.SHALLOW,
        )

    return _stub


def _writer_stub_body() -> object:
    """Writer stub: returns a minimal markdown body — no Ollama needed."""

    @node
    async def _stub(node_input: str) -> str:
        return (
            "## Executive Summary\n\nTokio and async-std differ in scheduler design.\n\n"
            "## s1\n\nSome prose about the subtopic.\n"
        )

    return _stub


# ---------------------------------------------------------------------------
# Runner factory (uses a temp DatabaseSessionService)
# ---------------------------------------------------------------------------


def _build_db_runner(db_url: str, workflow) -> Runner:
    """Build a Runner backed by a DatabaseSessionService at *db_url*."""
    svc = DatabaseSessionService(db_url=db_url)
    test_app = App(
        name=_APP_NAME,
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    return Runner(app=test_app, session_service=svc)


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------


def _extract_request_input(event) -> tuple[str, Any, str | None] | None:
    """Return ``(interrupt_id, payload, message)`` from an event, or None."""
    if not has_request_input_function_call(event):
        return None
    for part in event.content.parts:
        fc = getattr(part, "function_call", None)
        if fc and fc.name == "adk_request_input":
            args = fc.args or {}
            return fc.id, args.get("payload"), args.get("message")
    return None


async def _drive_until_cp1(
    runner: Runner,
    session_id: str,
    new_message: types.Content,
    invocation_id: str | None = None,
) -> tuple[str | None, Any, Any, str | None]:
    """Run until the FIRST CP1 RequestInput pause (or terminal output).

    Returns:
        ``(paused_invocation_id, cp1_interrupt_id, cp1_payload, final_output)``

        ``paused_invocation_id`` is the ``event.invocation_id`` recorded at the
        CP1 pause; ``None`` if the run reached terminal state without pausing.
    """
    paused_invocation_id: str | None = None
    cp1_interrupt_id: Any = None
    cp1_payload: Any = None
    final_output: Any = None

    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session_id,
        invocation_id=invocation_id,
        new_message=new_message,
    ):
        if event.output is not None:
            final_output = event.output

        extracted = _extract_request_input(event)
        if extracted is not None:
            iid, payload, _message = extracted
            # Identify CP1 by its payload type (plan dict, not a str draft).
            if not isinstance(payload, str) and not (
                isinstance(iid, str) and iid.startswith("cp3_")
            ):
                paused_invocation_id = event.invocation_id
                cp1_interrupt_id = iid
                cp1_payload = payload
                # Stop consuming events — we want to simulate an abrupt stop.
                break

    return paused_invocation_id, cp1_interrupt_id, cp1_payload, final_output


async def _drive_to_completion(
    runner: Runner,
    session_id: str,
    invocation_id: str,
    resume_message: types.Content,
) -> Any:
    """Resume from a paused invocation and drive to terminal output.

    Answers CP2 automatically (empty string = approve draft unchanged).
    Returns ``final_output`` (the ClaimLedger dict from the terminal event).
    """
    new_message = resume_message
    final_output: Any = None

    for _ in range(_MAX_HOPS):
        pending: tuple[str, Any] | None = None

        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if event.output is not None:
                final_output = event.output

            extracted = _extract_request_input(event)
            if extracted is not None:
                iid, payload, _message = extracted
                invocation_id = event.invocation_id
                # CP3 → done; CP2 → approve; anything else → approve
                if isinstance(iid, str) and iid.startswith("cp3_"):
                    reply: Any = {"result": "d"}
                elif isinstance(payload, str):
                    reply = {"result": ""}
                else:
                    reply = payload if isinstance(payload, dict) else {}
                pending = (iid, reply)

        if pending is None:
            break

        new_message = types.Content(
            role="user",
            parts=[create_request_input_response(pending[0], pending[1])],
        )

    return final_output


# ---------------------------------------------------------------------------
# The resume test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_cp1_pause(skip_if_no_ollama, tmp_path, monkeypatch):
    """E5.S2.T2: A run paused at CP1 can be resumed in a fresh runner from the same DB.

    Scenario:
    1. Build Runner #1 backed by a temp SQLite DatabaseSessionService.
    2. Drive the workflow until CP1 pause; record (session_id, invocation_id,
       cp1_interrupt_id, cp1_payload).  Do NOT answer the checkpoint yet.
    3. Discard Runner #1 (simulating a process kill).
    4. Build Runner #2 using the SAME DB file, a fresh App, and fresh stubs.
    5. Resume using the saved invocation_id; answer CP1 approval, then auto-
       drive through CP2 to terminal state.
    6. Assert: terminal output contains a valid ClaimLedger.
    """
    monkeypatch.setattr(config, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(config, "REPORTS_DIR", str(tmp_path / "reports"))

    # Temp DB isolated from the production sessions.db.
    db_path = tmp_path / "resume_test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # Use stub clarifier/planner/acquirer/extractor/verifier so only the
    # real Ollama model (via its LlmAgent default) is *not* needed for these
    # stages — the real clarifier IS used here because with _clarifier_stub_clear
    # we bypass its Ollama call, which is intentional: we want the test to focus
    # on the resume mechanic, not model behaviour.
    acq, ext, ver = make_generic_stubs()

    def _make_workflow():
        return build_research_workflow(
            clarifier_node=_clarifier_stub_clear(),
            planner_node=_planner_stub_shallow(),
            acquirer_node=acq,
            extractor_node=ext,
            verifier_node=ver,
            writer_node=_writer_stub_body(),
        )

    # ── Phase 1: run until CP1 pause ────────────────────────────────────────
    runner1 = _build_db_runner(db_url, _make_workflow())
    session = await runner1.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    session_id = session.id
    first_message = types.Content(
        role="user", parts=[types.Part(text=_RESUME_QUERY)]
    )

    paused_inv_id, cp1_iid, cp1_payload, early_output = await _drive_until_cp1(
        runner1, session_id, first_message
    )

    assert paused_inv_id is not None, (
        "Expected workflow to pause at CP1 but it reached terminal state. "
        "The clarifier or planner stub may not be injected correctly."
    )
    assert cp1_iid is not None, "CP1 interrupt_id not captured."

    print(f"\n[PASS] CP1 pause detected at invocation_id: {paused_inv_id}")

    # ── Phase 2: discard Runner #1, build Runner #2 from the same DB ────────
    del runner1  # simulate process kill — in-memory state is gone

    runner2 = _build_db_runner(db_url, _make_workflow())
    print("[PASS] Second runner reconstructed from same DB")

    # ── Phase 3: resume with CP1 approval ───────────────────────────────────
    # Approve CP1 as-is: return the plan payload unchanged.
    cp1_reply = cp1_payload if isinstance(cp1_payload, dict) else {}
    resume_message = types.Content(
        role="user",
        parts=[create_request_input_response(cp1_iid, cp1_reply)],
    )

    final_output = await _drive_to_completion(
        runner2,
        session_id,
        paused_inv_id,
        resume_message,
    )

    # ── Assertions ───────────────────────────────────────────────────────────
    assert final_output is not None, (
        "Run did not produce a terminal output after resume. "
        "Check that Runner #2 is connected to the correct session DB."
    )

    ledger = ClaimLedger.model_validate(final_output)
    assert len(ledger.claims) > 0, "Terminal ClaimLedger has no claims."

    print(f"[PASS] Run completed with terminal ClaimLedger ({len(ledger.claims)} claim(s))")

    # Summary evidence table
    print("\n" + "─" * 60)
    print("  E5.S2.T2 resume evidence")
    print("─" * 60)
    print(f"  session_id      : {session_id}")
    print(f"  invocation_id   : {paused_inv_id}")
    print(f"  cp1_interrupt_id: {cp1_iid}")
    print(f"  claims in ledger: {len(ledger.claims)}")
    print("─" * 60)
