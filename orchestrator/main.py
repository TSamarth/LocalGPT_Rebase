"""
Entry point for the A2A deep-research orchestrator.

Story 3.1 skeleton: the orchestrator walks a session through the full stage
machine with stub handlers (real agents replace the stubs as their tracks land).
This validates that the foundation (config + schemas + session store + stage
machine) drives a run end-to-end and reaches DONE.
"""
from __future__ import annotations

import sys

from app.config import config
from app.orchestrator import Orchestrator


def main(argv: list[str]) -> int:
    config.ensure_data_dirs()
    query = " ".join(argv).strip()
    if not query:
        print('usage: python main.py "your research query"')
        return 1

    orch = Orchestrator.start(query)
    print(f"session created: {orch.store.session_id}")
    print(f"  stage dir: {orch.store.dir}")
    print(f"  model:     {config.REASONING_MODEL}")
    final = orch.run_to_completion()
    print(f"  final stage: {final.value}  (agents are stubs — Story 3 tracks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
