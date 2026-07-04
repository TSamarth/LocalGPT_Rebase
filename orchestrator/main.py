"""
Entry point for the A2A deep-research orchestrator (v2, in-process).

Drives the v2 ADK dynamic-workflow to completion in-process over a resumable
``App`` (``app.adk_app.app``) + ``build_runner``. The same console checkpoint
renderers the REST CLI uses (``app.cli._answer_checkpoint`` → ``_render_cp1`` /
``_render_cp2`` / the CP3 verb grammar) answer the CP1/CP2/CP3 ``RequestInput``
pauses, and each reply resumes the run by ``invocation_id`` — the in-process
analog of ``app.cli.run_cli``'s over-the-wire loop.

On terminal the accumulated ``ClaimLedger`` summary is printed; the
``SessionExporterPlugin`` (registered on the App) writes the plan / ledger /
draft to ``data/sessions/{session_id}/`` — that path is printed too.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    has_request_input_function_call,
)
from google.genai import types

from app.adk_app import app
from app.cli import _answer_checkpoint, _request_input_call
from app.config import config
from app.runner import build_runner
from app.schemas import ClaimLedger, ClaimStatus

USER_ID = "console-user"

# Guard against a non-terminating resume loop (real runs need far fewer hops).
_MAX_HOPS = 200


async def _drive(runner, app_name: str, query: str) -> tuple[Any, str]:
    """Drive a run to its terminal ledger, answering each CP pause from the console.

    Mirrors the in-process resume harness (``tests/test_workflow`` /
    ``app.cli.run_cli``): run the runner; on a ``RequestInput`` pause, render the
    checkpoint + collect the human reply (``_answer_checkpoint``) and resume by
    ``invocation_id``; repeat until a terminal ``event.output`` appears.
    """
    session = await runner.session_service.create_session(app_name=app_name, user_id=USER_ID)
    print(f"session: {session.id}")

    new_message = types.Content(role="user", parts=[types.Part(text=query)])
    invocation_id: str | None = None
    final_output: Any = None

    for _ in range(_MAX_HOPS):
        pending: tuple[str, dict[str, Any]] | None = None
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
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

    return final_output, session.id


def main(argv: list[str]) -> int:
    logging.basicConfig(level=config.LOG_LEVEL)
    config.ensure_data_dirs()
    query = " ".join(argv).strip()
    if not query:
        print('usage: python main.py "your research query"')
        return 1

    runner = build_runner(app)
    print(f"model: {config.REASONING_MODEL}")
    final_output, session_id = asyncio.run(_drive(runner, app.name, query))

    print("\n=== Result ===")
    if final_output is not None:
        ledger = ClaimLedger.model_validate(final_output)
        kept = sum(1 for c in ledger.claims if c.status == ClaimStatus.KEPT)
        print(f"ledger: {len(ledger.claims)} claims ({kept} kept)")
    session_dir = Path(config.SESSIONS_DIR) / session_id
    print(f"draft:  {session_dir / 'draft.md'}")
    print(f"export: {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
