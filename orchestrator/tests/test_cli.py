"""E4.T3 gate — the thin REST CLI drives a FULL run over ``/run_sse`` offline.

Builds a DB-backed unified server whose ``localgpt_research`` app is the offline
``@node`` stub workflow (clarify -> CP1 -> loop -> CP2 -> ledger/draft), then runs
``app.cli.run_cli`` against it through an ``httpx.ASGITransport`` (no real port,
no model, no network). The CLI must:

  * create a session and POST ``/run_sse``,
  * pause at the CP1 plan-approval RequestInput and answer it (terminal stdin),
  * resume by ``invocation_id``, run the loop, pause at CP2 and answer it,
  * reach the terminal ``ClaimLedger`` (workflow ``event.output``) and surface
    the approved ``v2_draft`` — both read off the SSE stream.

GATE: the CLI completes the run over the API and the CP1/CP2 checkpoints
round-trip (proven by the returned ledger + draft). A second test exercises the
DEEP path so the CP3 ``apply_cp3_verbs`` branch round-trips too.

The REST runner is wired to the stub workflow via a custom ``BaseAgentLoader``
passed to the production ``get_fast_api_app`` factory, so the SAME factory the
real server uses runs the offline stubs over a temp sqlite DB (genuine
``invocation_id`` resume across the SSE turns).
"""
from __future__ import annotations

import warnings

import httpx
from google.adk.apps import App, ResumabilityConfig
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader

# Reuse the offline node stubs from the in-process workflow tests verbatim.
from test_workflow import (  # noqa: E402
    _acquirer_stub,
    _clarifier_stub,
    _extractor_stub,
    _kept_claim,
    _single_subtopic_planner_stub,
    _verifier_stub_from_passes,
    _writer_stub,
)

from app.cli import run_cli
from app.schemas import ClaimLedger, Depth
from app.server import APP_NAME
from app.workflow import build_research_workflow

# ResumabilityConfig EXPERIMENTAL warnings are expected and noisy.
warnings.filterwarnings("ignore", message=r".*ResumabilityConfig.*", category=UserWarning)


class _StubLoader(BaseAgentLoader):
    """Minimal agent loader that serves a pre-built stub ``App`` as ``APP_NAME``."""

    def __init__(self, app: App) -> None:
        self._app = app

    def load_agent(self, agent_name: str) -> App:
        return self._app

    def list_agents(self) -> list[str]:
        return [APP_NAME]


def _build_stub_server(tmp_path, workflow) -> httpx.ASGITransport:
    """Stand up the offline REST server over *workflow* on a temp sqlite DB."""
    stub_app = App(
        name=APP_NAME,
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cli.db"
    server_app = get_fast_api_app(
        agents_dir=str(tmp_path),  # discovery base; the injected loader serves the app
        agent_loader=_StubLoader(stub_app),
        session_service_uri=db_url,
        a2a=False,
        web=False,
    )
    return httpx.ASGITransport(app=server_app)


def _normal_workflow():
    """NORMAL 1-subtopic workflow: CP1 -> single loop pass (target met) -> CP2.

    NORMAL depth keeps CP3 out of the way, so the only pauses are CP1 and CP2.
    """
    return build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.NORMAL),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_verifier_stub_from_passes(
            [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
        ),
        writer_node=_writer_stub(),
    )


async def test_cli_drives_full_run_over_rest(tmp_path, monkeypatch):
    """THE GATE: the CLI drives clarify->CP1->loop->CP2->ledger/draft over REST."""
    transport = _build_stub_server(tmp_path, _normal_workflow())

    # Terminal stdin: approve CP1 ("a") then approve CP2 ("a"). The checkpoint
    # display helpers (cp1_checkpoint / cp2_checkpoint) read these via input().
    replies = iter(["a", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies))

    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        final_output, state = await run_cli("rust async runtimes", client=client)

    # The run reached the terminal ledger over the API...
    assert final_output is not None, "CLI never received a terminal output over REST"
    ledger = ClaimLedger.model_validate(final_output)
    assert {c.id for c in ledger.claims} == {"c1"}
    assert ledger.kept_count("s1") == 1
    # ...and the approved draft round-tripped via the v2_draft state delta.
    draft = state.get("v2_draft")
    assert draft is not None
    assert "## Coverage" in draft and "## Sources" in draft


async def test_cli_drives_deep_run_with_cp3_edit(tmp_path, monkeypatch):
    """DEEP path: CP3 fires per pass; the CLI's apply_cp3_verbs branch round-trips.

    One DEEP subtopic, single pass (target met). The CLI answers CP1 ("a"), the
    one CP3 pause with an exclude verb (``- 0`` then ``d``), and CP2 ("a"). The
    run still reaches the terminal ledger over REST.
    """
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_single_subtopic_planner_stub(1, Depth.DEEP),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_verifier_stub_from_passes(
            [ClaimLedger(claims=[_kept_claim("c1", "s1")])]
        ),
        writer_node=_writer_stub(),
    )
    transport = _build_stub_server(tmp_path, workflow)

    # CP1 approve; CP3 exclude index 0 then done; CP2 approve.
    replies = iter(["a", "- 0", "d", "a"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(replies))

    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8001"
    ) as client:
        final_output, state = await run_cli("rust async runtimes", client=client)

    assert final_output is not None
    ledger = ClaimLedger.model_validate(final_output)
    assert ledger.kept_count("s1") == 1
    assert state.get("v2_draft") is not None
