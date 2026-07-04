"""
URL scoring and triage tool.
Uses Crawl4AI's LinkPreviewConfig + score_links to evaluate and rank URLs
before deep crawling. Recommends the best crawl strategy per URL.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from crawl4ai import CrawlerRunConfig, LinkPreviewConfig
from fastmcp import Context

from app.config import config
from app.crawler import shared_crawler
from app.utils import get_cache_mode


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
    score_threshold: float = 0.3,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    max_links: int = 50,
    concurrency: int = 10,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Score and triage a list of URLs using Crawl4AI's link preview scoring.

    For each URL in the list, crawls it with score_links=True and
    LinkPreviewConfig to fetch head data (title, description) and compute:
    - intrinsic_score: URL quality & structural context (0–10)
    - contextual_score: BM25 relevance to query (0–1)
    - total_score: weighted combination (intrinsic×0.3 + contextual×0.7)

    Returns qualified URLs (above threshold) with recommended crawl strategy.

    Args:
        urls: Raw de-duplicated URL list from web search.
        query: Research query for BM25 contextual scoring.
        score_threshold: Minimum total_score to qualify a URL (default 0.3).
        include_patterns: URL glob patterns to include (e.g. ["*/docs/*"]).
        exclude_patterns: URL glob patterns to exclude (e.g. ["*/login*"]).
        max_links: Max links to evaluate per page (default 50).
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

    link_preview_cfg = LinkPreviewConfig(
        verbose=False,
        include_internal=True,
        include_external=True,
        max_links=max_links,
        include_patterns=include_patterns or [],
        exclude_patterns=exclude_patterns or [],
        concurrency=concurrency,
        timeout=15,
        query=query,
        score_threshold=0.0,  # We apply our own threshold after
    )

    run_cfg = CrawlerRunConfig(
        score_links=True,
        link_preview_config=link_preview_cfg,
        cache_mode=get_cache_mode(config.CACHE_MODE),
        page_timeout=config.PAGE_TIMEOUT_MS,
        excluded_tags=["nav", "footer", "script", "style"],
        verbose=False,
    )

    # Build a set for O(1) lookup
    url_set = set(urls)
    scored_map: Dict[str, Dict] = {}

    async with shared_crawler() as crawler:
        for i, url in enumerate(urls):
            if ctx:
                await ctx.info(f"  Scoring {i+1}/{len(urls)}: {url}")
            try:
                result = await crawler.arun(url, config=run_cfg)
                if not result.success:
                    scored_map[url] = {
                        "url": url,
                        "total_score": 0.0,
                        "intrinsic_score": 0.0,
                        "contextual_score": 0.0,
                        "title": "",
                        "description": "",
                        "error": result.error_message,
                    }
                    continue

                # Collect all links and find ones that match our input URL list
                all_links = (
                    result.links.get("internal", []) + result.links.get("external", [])
                )

                # Also score the page itself
                page_title = (result.metadata or {}).get("title", "") if result.metadata else ""
                page_desc = (result.metadata or {}).get("description", "") if result.metadata else ""

                # Find if this URL appears in scored links from its own page
                self_score = 0.0
                for lnk in all_links:
                    href = lnk.get("href", "")
                    if href == url or href.rstrip("/") == url.rstrip("/"):
                        self_score = lnk.get("total_score", 0.0)
                        break

                scored_map[url] = {
                    "url": url,
                    "total_score": self_score if self_score > 0 else _heuristic_score(url, query),
                    "intrinsic_score": 5.0,  # default when self-scored
                    "contextual_score": self_score,
                    "title": page_title,
                    "description": page_desc,
                    "error": None,
                }

                # Also capture any input URLs that appear as links on this page
                for lnk in all_links:
                    href = lnk.get("href", "")
                    if href in url_set and href not in scored_map:
                        head = lnk.get("head_data", {}) or {}
                        scored_map[href] = {
                            "url": href,
                            "total_score": lnk.get("total_score", 0.0),
                            "intrinsic_score": lnk.get("intrinsic_score", 0.0),
                            "contextual_score": lnk.get("contextual_score", 0.0),
                            "title": head.get("title", ""),
                            "description": (head.get("meta") or {}).get("description", ""),
                            "error": None,
                        }

            except Exception as e:
                scored_map[url] = {
                    "url": url,
                    "total_score": 0.0,
                    "intrinsic_score": 0.0,
                    "contextual_score": 0.0,
                    "title": "",
                    "description": "",
                    "error": str(e),
                }

    # Fill any URLs not yet scored
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
            qualified.append(entry)
        else:
            disqualified.append(entry)

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

