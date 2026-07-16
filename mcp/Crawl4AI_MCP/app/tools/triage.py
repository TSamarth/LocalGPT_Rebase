"""
URL scoring and triage tool.
Uses Crawl4AI's AsyncUrlSeeder to BM25-score URLs directly against the query
(via each URL's own <head> title/description) before deep crawling.
Recommends the best crawl strategy per URL.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from crawl4ai import SeedingConfig
from crawl4ai.async_url_seeder import AsyncUrlSeeder
from fastmcp import Context


def _recommend_strategy(url: str, score: float) -> str:
    """Heuristic: recommend crawl strategy based on score and URL shape."""
    url_lower = url.lower()
    doc_patterns = ["/docs/", "/documentation/", "/wiki/", "/guide/", "/manual/", "/reference/"]
    blog_patterns = ["/blog/", "/article/", "/post/", "/news/", "/tutorial/"]

    is_doc_site = any(p in url_lower for p in doc_patterns)
    is_blog = any(p in url_lower for p in blog_patterns)

    if score >= 0.75 and is_doc_site:
        return "adaptive_crawl"
    if score >= 0.55 and (is_doc_site or is_blog):
        return "deep_crawl"
    if score >= 0.35:
        return "crawl_url"
    return "skip"


async def score_and_triage_urls(
    urls: List[str],
    query: str,
    score_threshold: float = 0.5,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    max_links: int = 50,
    concurrency: int = 10,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Score and triage a list of URLs using Crawl4AI's BM25 head scoring.

    Fetches each URL's own <head> (title, meta description) via AsyncUrlSeeder
    and BM25-scores it against the query directly -- total_score lands in [0, 1].

    Returns qualified URLs (above threshold) with recommended crawl strategy.

    Args:
        urls: Raw de-duplicated URL list from web search.
        query: Research query for BM25 contextual scoring.
        score_threshold: Minimum total_score to qualify a URL (default 0.3).
        include_patterns: Unused (kept for backward-compat call signature).
        exclude_patterns: Unused (kept for backward-compat call signature).
        max_links: Unused (kept for backward-compat call signature).
        concurrency: Head-request concurrency (default 10).

    Returns:
        {
          "qualified_urls": [...],
          "disqualified_urls": [...],
          "stats": {...}
        }
    """
    if ctx:
        await ctx.info(f"Triaging {len(urls)} URLs for query: '{query}'")

    seed_cfg = SeedingConfig(
        extract_head=True,
        concurrency=concurrency,
        verbose=False,
        query=query,
        scoring_method="bm25",
    )

    scored_map: Dict[str, Dict] = {}

    async with AsyncUrlSeeder() as seeder:
        try:
            head_results = await seeder.extract_head_for_urls(
                urls, config=seed_cfg, concurrency=concurrency, timeout=15
            )
        except Exception as e:
            head_results = [
                {"url": url, "status": "failed", "head_data": {}, "error": str(e)}
                for url in urls
            ]

    for entry in head_results:
        url = str(entry["url"])
        if entry.get("status") != "valid":
            scored_map[url] = {
                "url": url,
                "total_score": _heuristic_score(url, query),
                "intrinsic_score": 0.0,
                "contextual_score": 0.0,
                "title": "",
                "description": "",
                "error": entry.get("error"),
            }
            continue

        head = entry.get("head_data", {}) or {}
        # relevance_score is BM25 over title/meta text vs. query, already in [0, 1]
        contextual_score = entry.get("relevance_score")
        total_score = contextual_score if contextual_score is not None else _heuristic_score(url, query)
        scored_map[url] = {
            "url": url,
            "total_score": total_score,
            "intrinsic_score": 0.0,
            "contextual_score": contextual_score or 0.0,
            "title": head.get("title") or "",
            "description": (head.get("meta") or {}).get("description", ""),
            "error": None,
        }

    # Fill any URLs not yet scored (shouldn't normally happen)
    for url in urls:
        if url not in scored_map:
            scored_map[url] = {
                "url": url,
                "total_score": _heuristic_score(url, query),
                "intrinsic_score": 0.0,
                "contextual_score": 0.0,
                "title": "",
                "description": "",
                "error": None,
            }

    # Partition into qualified / disqualified and add strategy
    qualified = []
    disqualified = []
    for entry in scored_map.values():
        strategy = _recommend_strategy(entry["url"], entry["total_score"])
        entry["recommended_strategy"] = strategy
        if entry["total_score"] >= score_threshold and strategy != "skip":
            entry["description"] = (entry.get("description") or "")[:200]
            qualified.append(entry)
        else:
            # Disqualified entries are discarded by the acquirer anyway --
            # keep only what's needed to explain why, dropping title/description
            # (which can carry KB-sized junk, e.g. hashtag-wall pages).
            slim = {
                "url": entry["url"],
                "total_score": entry["total_score"],
                "recommended_strategy": strategy,
            }
            if entry.get("error"):
                slim["error"] = entry["error"][:120]
            disqualified.append(slim)

    # Sort qualified by score desc
    qualified.sort(key=lambda x: x["total_score"], reverse=True)

    if ctx:
        await ctx.info(
            f"Triage complete: {len(qualified)} qualified, {len(disqualified)} disqualified"
        )

    return {
        "qualified_urls": qualified,
        "disqualified_urls": disqualified,
        "stats": {
            "total": len(urls),
            "qualified": len(qualified),
            "disqualified": len(disqualified),
            "score_threshold": score_threshold,
        },
    }


def _heuristic_score(url: str, query: str) -> float:
    """
    Fallback scoring when Crawl4AI scoring is unavailable.
    Simple BM25-inspired keyword match between URL tokens and query words.
    """
    url_tokens = set(re.split(r"[/\-_?=&.]", url.lower()))
    query_words = set(query.lower().split())
    overlap = url_tokens & query_words
    if not query_words:
        return 0.3
    return round(min(len(overlap) / len(query_words), 1.0) * 0.6, 3)

