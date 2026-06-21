"""
Adaptive crawl tool: confidence-based intelligent site exploration.

Wraps Crawl4AI's AdaptiveCrawler which automatically:
- Selects only relevant links to follow (BM25-based)
- Tracks information coverage confidence (0.0–1.0)
- Stops when target confidence is achieved or max_pages reached

Best for open-ended research where the full scope of relevant content
is unknown. The crawler self-directs based on the research query.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from crawl4ai import AdaptiveCrawler, AsyncWebCrawler, BrowserConfig
from fastmcp import Context

from app.config import config
from app.domain import registrable_domain
from app.storage.chroma_store import get_chroma
from app.storage.chunker import chunk_text
from app.storage.sqlite_store import get_store
from app.utils import make_id


async def adaptive_crawl(
    seed_url: str,
    query: str,
    session_id: Optional[str] = None,
    target_confidence: float = 0.8,
    max_pages: int = 30,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Adaptive confidence-based crawl using Crawl4AI's AdaptiveCrawler.

    The crawler intelligently selects which links to follow based on BM25
    relevance to the query, building confidence in information coverage.
    Stops automatically when target_confidence is reached or max_pages
    pages have been crawled.

    Best suited for: documentation sites, knowledge bases, and any site
    where the relevant content distribution is unknown upfront.

    Args:
        seed_url: Starting URL for adaptive exploration.
        query: Research query — drives link selection and confidence scoring.
        session_id: Research session identifier for storage grouping.
        target_confidence: Confidence threshold to stop early (0.0–1.0, default 0.8).
        max_pages: Safety cap on total pages crawled (default 30).

    Returns:
        Dict with crawled_urls, confidence_achieved, aggregated content, and stats.
    """
    if ctx:
        await ctx.info(
            f"Adaptive crawl: '{query}' starting at {seed_url} "
            f"(target confidence={target_confidence})"
        )

    browser_cfg = BrowserConfig(headless=True, text_mode=True, light_mode=True)

    crawled_urls: List[str] = []
    aggregated_fit_markdown: List[str] = []
    pages_with_content = 0

    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            adaptive = AdaptiveCrawler(crawler, max_pages=max_pages)

            digest_result = await adaptive.digest(
                start_url=seed_url,
                query=query,
            )

            confidence = getattr(adaptive, "confidence", 0.0)
            crawled_urls = list(getattr(digest_result, "crawled_urls", []))

            if ctx:
                await ctx.info(
                    f"AdaptiveCrawler finished: {len(crawled_urls)} pages, "
                    f"confidence={confidence:.2%}"
                )

            # Persist each crawled page's content
            store = await get_store()
            chroma = get_chroma()

            # AdaptiveCrawler doesn't expose per-page CrawlResult directly,
            # so we re-crawl each URL for storage with BM25 filter applied
            from crawl4ai import CrawlerRunConfig
            from crawl4ai.content_filter_strategy import BM25ContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

            from app.utils import get_cache_mode

            run_cfg = CrawlerRunConfig(
                markdown_generator=DefaultMarkdownGenerator(
                    content_filter=BM25ContentFilter(
                        user_query=query,
                        bm25_threshold=config.BM25_THRESHOLD,
                    )
                ),
                excluded_tags=["nav", "footer", "aside", "header", "script", "style"],
                remove_overlay_elements=True,
                cache_mode=get_cache_mode(config.CACHE_MODE),
                page_timeout=config.PAGE_TIMEOUT_MS,
                verbose=False,
            )

            for url in crawled_urls:
                try:
                    result = await crawler.arun(url, config=run_cfg)
                    page_id = make_id()
                    meta = result.metadata or {}
                    title = meta.get("title", "") if isinstance(meta, dict) else ""

                    raw_md = ""
                    fit_md = ""
                    if result.markdown:
                        if hasattr(result.markdown, "raw_markdown"):
                            raw_md = result.markdown.raw_markdown or ""
                            fit_md = result.markdown.fit_markdown or raw_md
                        else:
                            raw_md = str(result.markdown)
                            fit_md = raw_md

                    await store.save_page(
                        page_id=page_id,
                        session_id=session_id,
                        url=url,
                        title=title,
                        raw_markdown=raw_md,
                        fit_markdown=fit_md,
                        strategy="adaptive_crawl",
                        total_score=confidence,
                        status_code=result.status_code,
                        success=result.success,
                    )

                    if fit_md.strip():
                        pages_with_content += 1
                        aggregated_fit_markdown.append(f"## Source: {url}\n\n{fit_md}")

                        chunks = chunk_text(fit_md)
                        if chunks:
                            etld1 = registrable_domain(url)
                            chunk_ids = [make_id() for _ in chunks]
                            try:
                                chroma.add_chunks(
                                    chunk_ids,
                                    [c.text for c in chunks],
                                    [
                                        {
                                            "url": url,
                                            "title": title,
                                            "session_id": session_id or "",
                                            "query": query,
                                            "chunk_index": str(c.chunk_index),
                                            "strategy": "adaptive_crawl",
                                            "page_id": page_id,
                                            "etld1": etld1,
                                            "extraction": config.CHUNKING_STRATEGY,
                                        }
                                        for c in chunks
                                    ],
                                )
                            except Exception:
                                pass

                            chunk_records = [
                                {
                                    "id": cid,
                                    "chunk_index": c.chunk_index,
                                    "chunk_text": c.text,
                                    "token_count": c.token_count,
                                    "chroma_doc_id": cid,
                                    "etld1": etld1,
                                }
                                for cid, c in zip(chunk_ids, chunks)
                            ]
                            await store.save_chunks(page_id, chunk_records)

                except Exception as e:
                    if ctx:
                        await ctx.warning(f"  Failed to persist {url}: {e}")

    except Exception as e:
        if ctx:
            await ctx.error(f"AdaptiveCrawler error: {e}")
        return {
            "success": False,
            "seed_url": seed_url,
            "query": query,
            "error": str(e),
            "crawled_urls": [],
            "confidence_achieved": 0.0,
            "pages_with_content": 0,
            "aggregated_preview": "",
            "aggregated_word_count": 0,
            "stats": {},
            "session_id": session_id,
        }

    _aggregated = "\n\n---\n\n".join(aggregated_fit_markdown)
    return {
        "success": True,
        "seed_url": seed_url,
        "query": query,
        "confidence_achieved": round(confidence, 4),
        "pages_crawled": len(crawled_urls),
        "pages_with_content": pages_with_content,
        "crawled_urls": crawled_urls,
        # Full content is persisted + chunked; retrieve via search_chunks. Only a
        # preview travels in the agent's context to keep the payload small.
        "aggregated_preview": _aggregated[:500],
        "aggregated_word_count": len(_aggregated.split()),
        "stats": {
            "target_confidence": target_confidence,
            "stopped_early": confidence >= target_confidence,
            "max_pages": max_pages,
        },
        "session_id": session_id,
    }

