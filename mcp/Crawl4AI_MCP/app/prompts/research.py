"""MCP Prompt: Deep Research Plan."""
from __future__ import annotations

from typing import List, Optional


def deep_research_plan(
    topic: str,
    urls: Optional[List[str]] = None,
    target_depth: str = "comprehensive",
) -> str:
    """
    Generate a step-by-step research execution plan for the ADK agent.

    Args:
        topic: Research topic or question to investigate.
        urls: Optional initial URL list from web search.
        target_depth: Research depth level — "quick" | "standard" | "comprehensive".

    Returns:
        Markdown-formatted research plan with tool-use instructions.
    """
    depth_config = {
        "quick":         {"max_pages": 5,  "max_depth": 1, "confidence": 0.6},
        "standard":      {"max_pages": 15, "max_depth": 2, "confidence": 0.75},
        "comprehensive": {"max_pages": 30, "max_depth": 3, "confidence": 0.85},
    }
    cfg = depth_config.get(target_depth, depth_config["comprehensive"])

    url_section = ""
    if urls:
        url_list = "\n".join(f"  - {u}" for u in urls[:10])
        extra = f"\n  (... and {len(urls)-10} more)" if len(urls) > 10 else ""
        url_section = f"\n## Available URLs\n{url_list}{extra}\n"

    session_id = topic.lower().replace(" ", "_")[:40] + "_session"

    return (
        f"# Deep Research Plan: {topic}\n\n"
        f"## Objective\nConduct {target_depth} research on: **{topic}**\n"
        f"{url_section}\n"
        f"## Execution Steps\n\n"
        f"### Step 0 — Discover URLs\n"
        f"```\nTool: discover_urls\n"
        f"  query: \"{topic}\"\n"
        f"  sources: [\"duckduckgo\", \"arxiv\", \"semantic_scholar\"]\n"
        f"  triage: false\n```\n"
        f"(Skip if you already have a URL list above.)\n\n"
        f"### Step 1 — Triage URLs (ALWAYS run before crawling)\n"
        f"```\nTool: score_and_triage_urls\n"
        f"  urls: <from discover_urls or web search>\n"
        f"  query: \"{topic}\"\n"
        f"  score_threshold: 0.3\n"
        f"  session_id: \"{session_id}\"\n```\n\n"
        f"### Step 2A — Adaptive Crawl (strategy=adaptive_crawl)\n"
        f"```\nTool: adaptive_crawl\n"
        f"  seed_url: <url from triage>\n"
        f"  query: \"{topic}\"\n"
        f"  target_confidence: {cfg['confidence']}\n"
        f"  max_pages: {cfg['max_pages']}\n"
        f"  session_id: \"{session_id}\"\n```\n\n"
        f"### Step 2B — Deep Crawl (strategy=deep_crawl)\n"
        f"```\nTool: deep_crawl\n"
        f"  seed_url: <url from triage>\n"
        f"  query: \"{topic}\"\n"
        f"  max_depth: {cfg['max_depth']}\n"
        f"  max_pages: {cfg['max_pages']}\n"
        f"  stay_on_domain: true\n"
        f"  session_id: \"{session_id}\"\n```\n\n"
        f"### Step 2C — Batch Crawl (strategy=crawl_url)\n"
        f"```\nTool: crawl_many\n"
        f"  urls: <list of individual page URLs>\n"
        f"  query: \"{topic}\"\n"
        f"  max_concurrent: 5\n"
        f"  session_id: \"{session_id}\"\n```\n\n"
        f"### Step 3 — Retrieve Relevant Chunks\n"
        f"```\nTool: search_chunks\n"
        f"  query: \"{topic}\"\n"
        f"  n_results: 15\n"
        f"  session_id: \"{session_id}\"\n```\n\n"
        f"### Step 4 — Verify Coverage\n"
        f"```\nTool: get_crawl_stats\n  session_id: \"{session_id}\"\n```\n\n"
        f"## Quality Notes\n"
        f"- **fit_markdown** is query-filtered high-quality content — use for synthesis\n"
        f"- **raw_markdown** preserves full page content\n"
        f"- Chunks from search_chunks include source URL and title for citation\n"
        f"- Pages with total_score > 0.7 are the highest quality sources\n"
    )
