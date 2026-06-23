"""
Setup probe (Story 1, task 1.3 — Gate G2).

Verifies the local environment can host the pipeline BEFORE building the agent
layer on top of it:
  1. Ollama reachable at OLLAMA_BASE_URL.
  2. Reasoning model (or fallback) + embedding model are pulled.
  3. Reports VRAM use of any currently-loaded models (via `ollama ps`),
     so you can confirm the < 14 GB envelope (architecture.md §1).

VRAM is reported, not asserted — true usage only shows once a model is loaded
with a real context. Load one (`ollama run <model> "hi"`) then re-run this.

Run:  python scripts/check_setup.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

# Allow running as a loose script (python scripts/check_setup.py).
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.config import config  # noqa: E402


def _get(path: str) -> dict:
    url = config.OLLAMA_BASE_URL.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def check_ollama() -> list[str]:
    """Return list of installed model names, or raise if Ollama unreachable."""
    data = _get("/api/tags")
    return [m["name"] for m in data.get("models", [])]


def report_loaded_vram() -> None:
    """Print `ollama ps` so the user can read VRAM/size of loaded models."""
    try:
        out = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=10
        )
        print(out.stdout.strip() or "  (no models currently loaded)")
    except (FileNotFoundError, subprocess.SubprocessError):
        print("  (could not run `ollama ps` — is the ollama CLI on PATH?)")


def main() -> int:
    print(f"Ollama URL: {config.OLLAMA_BASE_URL}")
    try:
        installed = check_ollama()
        print(f"installed: {installed}")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  FAIL: Ollama not reachable ({exc}). Start it with `ollama serve`.")
        return 1
    print(f"  OK: reachable, {len(installed)} model(s) installed.")

    ok = True
    reasoning_present = config.REASONING_MODEL in installed or config.REASONING_MODEL_FALLBACK in installed
    if reasoning_present:
        which = config.REASONING_MODEL if config.REASONING_MODEL in installed else config.REASONING_MODEL_FALLBACK
        print(f"  OK: reasoning model present ({which}).")
    else:
        ok = False
        print("  MISSING reasoning model. Pull one:")
        print(f"    ollama pull {config.REASONING_MODEL}")
        print(f"    ollama pull {config.REASONING_MODEL_FALLBACK}   # lighter fallback")

    if config.EMBED_MODEL in installed:
        print(f"  OK: embedding model present ({config.EMBED_MODEL}).")
    else:
        ok = False
        print(f"  MISSING embedding model. Pull it:  ollama pull {config.EMBED_MODEL}")

    print("\nLoaded models / VRAM (`ollama ps`):")
    report_loaded_vram()
    print("\nTarget: reasoning + embed resident under ~14 GB VRAM (architecture.md §1).")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
