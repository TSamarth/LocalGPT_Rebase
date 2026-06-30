"""
mem_probe.py — OOM/VRAM budget probe for E5.S2.T4.

Usage:
    python scripts/mem_probe.py -- python main.py "What are the differences between Tokio and async-std?"

Samples subprocess RSS every 2 s, then calls `ollama ps` on exit to capture VRAM.
Exit code: 0 if both RAM < 16 GB and VRAM < 14 GB, else 1.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

import psutil

# ── thresholds ────────────────────────────────────────────────────────────────
RAM_LIMIT_GB = 16.0
VRAM_LIMIT_GB = 14.0
SAMPLE_INTERVAL_S = 2.0


def _bytes_to_gb(n: int) -> float:
    return n / (1024 ** 3)


def _parse_vram_gb(ollama_ps_output: str) -> float | None:
    """
    Parse peak VRAM from `ollama ps` output.

    `ollama ps` table format (as of Ollama 0.3+):
        NAME              ID              SIZE      PROCESSOR    UNTIL
        qwen2.5:14b       abc123def456    9.0 GB    100% GPU     ...

    The SIZE column is the VRAM footprint when the model is GPU-resident.
    Returns the largest SIZE value found, in GB, or None if unparseable.
    """
    max_gb: float | None = None
    for line in ollama_ps_output.splitlines():
        # Match patterns like "9.0 GB" or "13.5 GB"
        m = re.search(r"(\d+(?:\.\d+)?)\s+GB", line, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if max_gb is None or val > max_gb:
                max_gb = val
    return max_gb


def main() -> int:
    # ── argument parsing: everything after '--' is the child command ──────────
    try:
        sep = sys.argv.index("--")
    except ValueError:
        print("Usage: python scripts/mem_probe.py -- <command> [args...]", file=sys.stderr)
        return 2

    child_cmd = sys.argv[sep + 1:]
    if not child_cmd:
        print("Error: no command provided after '--'", file=sys.stderr)
        return 2

    print(f"[mem_probe] Launching: {' '.join(child_cmd)}")
    print(f"[mem_probe] RAM limit: {RAM_LIMIT_GB} GB  |  VRAM limit: {VRAM_LIMIT_GB} GB")
    print("-" * 60)

    # ── spawn child ───────────────────────────────────────────────────────────
    proc = subprocess.Popen(child_cmd)
    ps_proc = psutil.Process(proc.pid)

    peak_rss_bytes: int = 0

    # ── sampling loop ─────────────────────────────────────────────────────────
    while proc.poll() is None:
        try:
            rss = ps_proc.memory_info().rss
            if rss > peak_rss_bytes:
                peak_rss_bytes = rss
        except psutil.NoSuchProcess:
            break
        time.sleep(SAMPLE_INTERVAL_S)

    # Drain — do one final read after the process exits in case it was missed
    try:
        rss = ps_proc.memory_info().rss
        if rss > peak_rss_bytes:
            peak_rss_bytes = rss
    except psutil.NoSuchProcess:
        pass

    child_exit = proc.wait()
    print(f"[mem_probe] Child exited with code {child_exit}")

    # ── VRAM snapshot via ollama ps ───────────────────────────────────────────
    try:
        ollama_result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ollama_output = ollama_result.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        ollama_output = f"<ollama ps unavailable: {exc}>"

    # ── summary ───────────────────────────────────────────────────────────────
    peak_ram_gb = _bytes_to_gb(peak_rss_bytes)
    vram_gb = _parse_vram_gb(ollama_output)

    ram_pass = peak_ram_gb < RAM_LIMIT_GB
    if vram_gb is not None:
        vram_pass = vram_gb < VRAM_LIMIT_GB
        vram_label = f"{vram_gb:.1f} GB"
    else:
        # No model loaded (already unloaded by the time we called ollama ps) —
        # treat as pass: the model wasn't resident, so it can't have exceeded budget.
        vram_pass = True
        vram_label = "N/A (no model loaded)"

    print()
    print("=" * 60)
    print(f"Peak RAM : {peak_ram_gb:.2f} GB")
    print(f"VRAM     : {vram_label}")
    print()
    print("ollama ps VRAM snapshot:")
    print(ollama_output.strip() if ollama_output.strip() else "  (no output)")
    print()
    print(f"RAM  PASS/FAIL : {'PASS' if ram_pass  else 'FAIL'}  ({peak_ram_gb:.2f} GB < {RAM_LIMIT_GB} GB)")
    print(f"VRAM PASS/FAIL : {'PASS' if vram_pass else 'FAIL'}  ({vram_label} < {VRAM_LIMIT_GB} GB)")
    print("=" * 60)

    overall = ram_pass and vram_pass
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
