"""
End-to-end test for the Crawl4AI MCP server.

Runs the full research workflow against real URLs using the FastMCP Client
connected directly to the server (in-process, no subprocess needed).

Workflow:
  1. Discover  — list_tools / list_resources / list_prompts
  2. Triage    — score_and_triage_urls
  3. Crawl     — crawl_url (single)
  4. Batch     — crawl_many
  5. Search    — search_chunks (semantic)
  6. Stats     — get_crawl_stats

Usage:
    uv run python e2e_test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import time
from typing import Any

from fastmcp import Client

from app.server import mcp

# ── ANSI colours ─────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAGENTA = "\033[35m"

def _c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"

def header(title: str) -> None:
    bar = "─" * 60
    print(f"\n{_c(BOLD + CYAN, bar)}")
    print(f"{_c(BOLD + CYAN, f'  {title}')}")
    print(f"{_c(BOLD + CYAN, bar)}")

def step(n: int, label: str) -> None:
    print(f"\n{_c(BOLD + BLUE, f'[Step {n}]')} {_c(BOLD, label)}")

def ok(msg: str) -> None:
    print(f"  {_c(GREEN, '✓')} {msg}")

def warn(msg: str) -> None:
    print(f"  {_c(YELLOW, '⚠')} {msg}")

def fail(msg: str) -> None:
    print(f"  {_c(RED, '✗')} {msg}")

def info(label: str, value: Any) -> None:
    val_str = str(value)
    if len(val_str) > 120:
        val_str = val_str[:117] + "..."
    print(f"  {_c(DIM, label + ':')} {val_str}")

def section(label: str) -> None:
    print(f"\n  {_c(MAGENTA + BOLD, label)}")

def json_preview(data: Any, max_chars: int = 400) -> None:
    """Pretty-print a JSON-serialisable value, truncated."""
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        text = str(data)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n  {_c(DIM, '... (truncated)')}"
    for line in text.splitlines():
        print(f"    {line}")


def _parse(result: Any) -> Any:
    """Extract parsed data from a FastMCP CallToolResult.

    FastMCP 3.x returns a CallToolResult with:
      .data             — already-parsed Python dict/list (preferred)
      .content[0].text  — raw JSON string (fallback)
    """
    # Parse from the first text content item (the full JSON envelope)
    if hasattr(result, "content") and result.content:
        text = getattr(result.content[0], "text", "")
    elif isinstance(result, list) and result:
        text = getattr(result[0], "text", str(result[0]))
    else:
        text = str(result)

    try:
        parsed = json.loads(text)
    except Exception:
        return text

    # Unwrap the standard {ok, error, data} ToolResponse envelope.
    if isinstance(parsed, dict) and "ok" in parsed and "data" in parsed:
        return parsed["data"]
    return parsed


# ── Test parameters ───────────────────────────────────────────────────────────
SESSION_ID = "e2e-test-session"

# Real, stable documentation URLs for the research query
RESEARCH_QUERY = "Python asyncio async await coroutines"

TRIAGE_URLS = [
    "https://docs.python.org/3/library/asyncio.html",
    "https://docs.python.org/3/library/asyncio-task.html",
    "https://realpython.com/async-io-python/",
    "https://example.com/",                                # low-relevance control
]

SINGLE_CRAWL_URL = "https://docs.python.org/3/library/asyncio-task.html"

BATCH_URLS = [
    "https://docs.python.org/3/library/asyncio-event-loop.html",
    "https://docs.python.org/3/library/asyncio-sync.html",
]


# ── Main test flow ────────────────────────────────────────────────────────────
async def run_e2e() -> int:
    """Run all E2E steps. Returns number of failures."""
    header("Crawl4AI MCP — End-to-End Test")
    print(f"  Session : {_c(BOLD, SESSION_ID)}")
    print(f"  Query   : {_c(BOLD, RESEARCH_QUERY)}")

    failures = 0
    t_total = time.perf_counter()

    async with Client(mcp) as client:

        # ── Step 1: Discovery ────────────────────────────────────────────────
        step(1, "Server Discovery")
        try:
            tools     = await client.list_tools()
            resources = await client.list_resources()
            prompts   = await client.list_prompts()

            tool_names = [t.name for t in tools]
            expected   = {
                "score_and_triage_urls", "crawl_url", "crawl_many",
                "deep_crawl", "adaptive_crawl", "search_chunks", "get_crawl_stats",
            }
            missing = expected - set(tool_names)

            if not missing:
                ok(f"All {len(expected)} tools registered")
            else:
                fail(f"Missing tools: {missing}")
                failures += 1

            info("Tools",     ", ".join(tool_names))
            info("Resources", [r.uri for r in resources])
            info("Prompts",   [p.name for p in prompts])

        except Exception as exc:
            fail(f"Discovery failed: {exc}")
            failures += 1

        # ── Step 2: Read resources ───────────────────────────────────────────
        step(2, "MCP Resources")
        for uri in ("crawl4ai://status", "crawl4ai://capabilities"):
            try:
                content = await client.read_resource(uri)
                text = content[0].text if isinstance(content, list) else str(content)
                ok(f"Read {uri}")
                info("Preview", text[:120].replace("\n", " "))
            except Exception as exc:
                fail(f"{uri} — {exc}")
                failures += 1

        # ── Step 3: Prompt ───────────────────────────────────────────────────
        step(3, "MCP Prompt — deep_research_plan")
        try:
            result = await client.get_prompt(
                "deep_research_plan",
                {"topic": RESEARCH_QUERY, "depth": "standard"},
            )
            msgs = result.messages if hasattr(result, "messages") else result
            ok(f"Prompt returned {len(msgs)} message(s)")
            if msgs:
                first = msgs[0]
                preview = getattr(getattr(first, "content", first), "text", str(first))
                info("Snippet", preview[:150].replace("\n", " "))
        except Exception as exc:
            warn(f"Prompt unavailable (non-fatal): {exc}")

        # ── Step 4: Triage ───────────────────────────────────────────────────
        step(4, "score_and_triage_urls")
        t0 = time.perf_counter()
        try:
            raw = await client.call_tool(
                "score_and_triage_urls",
                {
                    "urls": TRIAGE_URLS,
                    "query": RESEARCH_QUERY,
                    "score_threshold": 0.2,
                },
            )
            data = _parse(raw)
            elapsed = time.perf_counter() - t0

            stats = data.get("stats", {})
            ok(
                f"Triage complete in {elapsed:.1f}s — "
                f"{stats.get('qualified', '?')} qualified / "
                f"{stats.get('total', '?')} total"
            )

            section("Qualified URLs")
            for entry in data.get("qualified_urls", []):
                score    = entry.get("total_score", 0)
                strategy = entry.get("recommended_strategy", "?")
                url      = entry.get("url", "")
                print(
                    f"    {_c(GREEN, f'{score:.3f}')}  "
                    f"{_c(YELLOW, f'[{strategy}]')}  {url}"
                )

            section("Disqualified URLs")
            for entry in data.get("disqualified_urls", []):
                url   = entry.get("url", "")
                score = entry.get("total_score", 0)
                print(f"    {_c(DIM, f'{score:.3f}')}  {url}")

        except Exception as exc:
            fail(f"Triage failed: {exc}")
            failures += 1

        # ── Step 5: crawl_url ────────────────────────────────────────────────
        step(5, f"crawl_url — {SINGLE_CRAWL_URL}")
        t0 = time.perf_counter()
        try:
            raw = await client.call_tool(
                "crawl_url",
                {
                    "url": SINGLE_CRAWL_URL,
                    "query": RESEARCH_QUERY,
                    "session_id": SESSION_ID,
                    "cache_mode": "bypass",
                },
            )
            data    = _parse(raw)
            elapsed = time.perf_counter() - t0

            success = data.get("success", False)
            marker  = _c(GREEN, "✓ Success") if success else _c(RED, "✗ Failed")

            ok(f"{marker} in {elapsed:.1f}s")
            info("page_id",       data.get("page_id"))
            info("title",         data.get("title", "(none)"))
            info("status_code",   data.get("metadata", {}).get("status_code"))
            info("raw_words",     data.get("metadata", {}).get("word_count"))
            info("fit_words",     data.get("metadata", {}).get("fit_word_count"))
            info("internal_links",len(data.get("internal_links", [])))

            section("fit_preview snippet")
            snippet = (data.get("fit_preview") or "")[:500]
            for line in textwrap.wrap(snippet, 80):
                print(f"    {_c(DIM, line)}")

            if not success:
                failures += 1

        except Exception as exc:
            fail(f"crawl_url failed: {exc}")
            failures += 1

        # ── Step 6: crawl_many ───────────────────────────────────────────────
        step(6, f"crawl_many — {len(BATCH_URLS)} URLs")
        t0 = time.perf_counter()
        try:
            raw = await client.call_tool(
                "crawl_many",
                {
                    "urls": BATCH_URLS,
                    "query": RESEARCH_QUERY,
                    "session_id": SESSION_ID,
                    "cache_mode": "bypass",
                    "max_concurrent": 2,
                },
            )
            data    = _parse(raw)
            elapsed = time.perf_counter() - t0

            stats   = data.get("stats", {})
            ok(
                f"Batch done in {elapsed:.1f}s — "
                f"{stats.get('success', 0)} succeeded, "
                f"{stats.get('failed', 0)} failed"
            )

            section("Results")
            for r in data.get("results", []):
                marker = _c(GREEN, "✓") if r.get("success") else _c(RED, "✗")
                words  = (r.get("metadata") or {}).get("fit_word_count", 0)
                print(f"    {marker} [{words:>5} words]  {r.get('url', '')}")

            if stats.get("failed", 0) > 0:
                warn(f"{stats['failed']} URL(s) failed to crawl")

        except Exception as exc:
            fail(f"crawl_many failed: {exc}")
            failures += 1

        # ── Step 7: search_chunks ────────────────────────────────────────────
        step(7, f"search_chunks — '{RESEARCH_QUERY}'")
        t0 = time.perf_counter()
        try:
            raw = await client.call_tool(
                "search_chunks",
                {
                    "query": RESEARCH_QUERY,
                    "n_results": 5,
                    "session_id": SESSION_ID,
                },
            )
            data    = _parse(raw)
            elapsed = time.perf_counter() - t0

            error   = data.get("error")
            found   = data.get("total_found", 0)

            if error:
                warn(f"Search returned error (Ollama may be down): {error}")
            else:
                ok(f"Found {found} chunks in {elapsed:.1f}s")
                section("Top chunks")
                for i, chunk in enumerate(data.get("chunks", [])[:3], 1):
                    score = chunk.get("score") or chunk.get("distance", "n/a")
                    url   = chunk.get("metadata", {}).get("url", "")
                    text  = (chunk.get("text") or chunk.get("document", ""))[:120]
                    print(
                        f"    {_c(BOLD, f'#{i}')} "
                        f"{_c(GREEN, f'score={score:.4f}' if isinstance(score, float) else str(score))}"
                        f"  {_c(DIM, url)}"
                    )
                    print(f"       {_c(DIM, text)}")

        except Exception as exc:
            fail(f"search_chunks failed: {exc}")
            failures += 1

        # ── Step 8: get_crawl_stats ──────────────────────────────────────────
        step(8, "get_crawl_stats")
        try:
            raw  = await client.call_tool(
                "get_crawl_stats",
                {"session_id": SESSION_ID},
            )
            data = _parse(raw)

            ok("Stats retrieved")
            sqlite  = data.get("sqlite", {})
            chroma  = data.get("chromadb", {})

            section("SQLite")
            for k, v in sqlite.items():
                info(f"  {k}", v)

            section("ChromaDB")
            info("  total_chunks", chroma.get("total_chunks", "n/a"))
            info("  embed_model",  chroma.get("embed_model",  "n/a"))

        except Exception as exc:
            fail(f"get_crawl_stats failed: {exc}")
            failures += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - t_total
    header("Summary")
    if failures == 0:
        print(f"  {_c(BOLD + GREEN, 'ALL STEPS PASSED')}  ({total_elapsed:.1f}s total)")
    else:
        print(
            f"  {_c(BOLD + RED, f'{failures} STEP(S) FAILED')}  "
            f"({total_elapsed:.1f}s total)"
        )
    print()
    return failures


if __name__ == "__main__":
    exit_code = asyncio.run(run_e2e())
    sys.exit(exit_code)


