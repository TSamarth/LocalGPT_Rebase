"""
Entry point for the A2A deep-research orchestrator.

Story 1 scaffold: wiring the agent stage machine comes in later stories
(see claudedocs/workflow_a2a_research.md). For now this validates that the
foundation (config + schemas + session store) loads and a session can be
created end-to-end.
"""
from __future__ import annotations

import sys

from app.config import config
from app.session import SessionStore


def main(argv: list[str]) -> int:
    config.ensure_data_dirs()
    query = " ".join(argv).strip()
    if not query:
        print('usage: python main.py "your research query"')
        return 1

    store = SessionStore.create(query)
    print(f"session created: {store.session_id}")
    print(f"  stage dir: {store.dir}")
    print(f"  model:     {config.REASONING_MODEL}")
    print("  (agent pipeline not yet wired — Story 3+)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
