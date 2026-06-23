"""
Entry point for the A2A deep-research orchestrator.

Story 5: the orchestrator runs the full pipeline live — the composition root
(``app.pipeline``) wires the six real agents + the CP1/CP2/CP3 human checkpoints,
and the research loop runs Acquire→[CP3]→Extract→Verify per subtopic under the
stop-rule. Drives a session end-to-end against Ollama + the crawl4ai MCP server
and publishes the approved report to ``data/reports/{session_id}.md``.
"""
from __future__ import annotations

import sys

from app.config import config
from app.pipeline import build_orchestrator, run_pipeline


def main(argv: list[str]) -> int:
    config.ensure_data_dirs()
    query = " ".join(argv).strip()
    if not query:
        print('usage: python main.py "your research query"')
        return 1

    orch = build_orchestrator(query)
    print(f"session created: {orch.store.session_id}")
    print(f"  stage dir: {orch.store.dir}")
    print(f"  model:     {config.REASONING_MODEL}")
    final = run_pipeline(orch)
    print(f"  final stage: {final.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
