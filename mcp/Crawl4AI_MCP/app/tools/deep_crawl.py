"""
Deep crawl tool: BFS link-following from a seed URL.

Explores a site breadth-first, following internal links up to max_depth
levels and max_pages total. Ideal for documentation sites, wikis, and
blog archives where the agent knows the seed but not all relevant pages.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from crawl4ai import CrawlerRunConfig
from crawl4ai.content_filter_strategy import BM25ContentFilter, PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from fastmcp import Context

from app.config import config
from app.crawler import shared_crawler
from app.tools.crawl import _build_llm_extraction_strategy, _format_result, _persist_result
from app.utils import get_cache_mode


def _same_domain(url: str, seed: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(seed).netloc
    except Exception:
        return False


def _matches_pattern(url: str, pattern: Optional[str]) -> bool:
    if not pattern:
        return True
    try:
        return bool(re.search(pattern, url))
    except re.error:
        return url.find(pattern) >= 0


async def deep_crawl(
    seed_url: str,
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = 20,
    url_pattern: Optional[str] = None,
    stay_on_domain: bool = True,
    score_threshold: float = 0.0,
    cache_mode: Optional[str] = None,
    use_llm_extraction: Optional[bool] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    BFS deep crawl starting from seed_url, following internal links.

    Explores the site in breadth-first order: crawls the seed, collects all
    internal links, filters them by url_pattern/stay_on_domain, then crawls
    those pages at depth+1, repeating until max_depth or max_pages is reached.

    Generates fit_markdown per page and stores results in SQLite + ChromaDB.
    Content is chunked using the configured Crawl4AI chunking strategy.

    Args:
        seed_url: Starting URL for the BFS crawl.
        query: Research query for BM25 content filtering and storage.
        session_id: Research session identifier.
        max_depth: Maximum link-following depth from seed (default 2).
        max_pages: Hard cap on total pages crawled (default 20).
        url_pattern: Regex pattern — only follow links matching this (e.g. ".*/docs/.*").
        stay_on_domain: If True, only follow links on the same domain (default True).
        score_threshold: Minimum intrinsic_score for a link to be followed (0 = all links).
        cache_mode: Override cache behaviour: enabled | bypass | disabled.
        use_llm_extraction: Apply LLM extraction for richer chunks (overrides
            config.LLM_EXTRACTION_ENABLED when specified).

    Returns:
        Dict with pages list, aggregated stats, and crawl summary.
    """
    if ctx:
        await ctx.info(f"Deep crawl starting at: {seed_url} (max_depth={max_depth}, max_pages={max_pages})")

    llm_extract = use_llm_extraction if use_llm_extraction is not None else config.LLM_EXTRACTION_ENABLED

    # Build content filter
    if query:
        content_filter = BM25ContentFilter(
            user_query=query,
            bm25_threshold=config.BM25_THRESHOLD,
        )
    else:
        content_filter = PruningContentFilter(
            threshold=config.PRUNING_THRESHOLD,
            threshold_type="dynamic",
            min_word_threshold=config.MIN_WORD_THRESHOLD,
        )

    # Optional LLM extraction strategy
    extraction_strategy = _build_llm_extraction_strategy(query) if llm_extract else None

    run_cfg = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(content_filter=content_filter),
        excluded_tags=["nav", "footer", "aside", "header", "script", "style"],
        remove_overlay_elements=True,
        remove_forms=True,
        exclude_social_media_links=True,
        score_links=score_threshold > 0.0,
        cache_mode=get_cache_mode(cache_mode or config.CACHE_MODE),
        page_timeout=config.PAGE_TIMEOUT_MS,
        verbose=False,
        extraction_strategy=extraction_strategy,
    )

    # BFS state
    queue: deque = deque([(seed_url, 0)])
    visited: set = set()
    results_out: List[Dict] = []
    success_count = 0
    fail_count = 0
    deepest_depth = 0

    async with shared_crawler() as crawler:
        while queue and len(results_out) < max_pages:
            url, depth = queue.popleft()

            if url in visited:
                continue
            visited.add(url)

            if ctx:
                await ctx.info(f"  [{depth}] Crawling: {url}")

            try:
                result = await crawler.arun(url, config=run_cfg)
            except Exception as e:
                if ctx:
                    await ctx.warning(f"  Error crawling {url}: {e}")
                continue

            page_id = await _persist_result(result, session_id, "deep_crawl", query)
            results_out.append(_format_result(result, page_id))
            deepest_depth = max(deepest_depth, depth)

            if result.success:
                success_count += 1
            else:
                fail_count += 1
                continue

            # Discover next-level links
            if depth < max_depth:
                internal_links = result.links.get("internal", []) if result.links else []

                for lnk in internal_links:
                    href = lnk.get("href", "")
                    if not href or not href.startswith("http"):
                        continue
                    if href in visited:
                        continue
                    if stay_on_domain and not _same_domain(href, seed_url):
                        continue
                    if not _matches_pattern(href, url_pattern):
                        continue
                    if score_threshold > 0:
                        link_score = lnk.get("intrinsic_score", 0.0) or 0.0
                        if link_score < score_threshold:
                            continue
                    if len(results_out) + len(queue) < max_pages:
                        queue.append((href, depth + 1))

    if ctx:
        await ctx.info(
            f"Deep crawl done: {success_count} pages, deepest depth={deepest_depth}"
        )

    total_fit_words = sum(
        r["metadata"].get("fit_word_count", 0) for r in results_out
    )

    return {
        "pages": results_out,
        "stats": {
            "seed_url": seed_url,
            "total_pages": len(results_out),
            "success": success_count,
            "failed": fail_count,
            "deepest_depth_reached": deepest_depth,
            "total_fit_words": total_fit_words,
        },
        "session_id": session_id,
    }

