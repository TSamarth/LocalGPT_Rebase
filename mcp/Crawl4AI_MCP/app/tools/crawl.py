"""
Core crawling tools: crawl_url and crawl_many.

Both tools select a content filter based on whether a query is given:
  - query provided → BM25ContentFilter (query-focused relevance)
  - no query       → PruningContentFilter (boilerplate removal)
Only the markdown produced by the selected filter becomes fit_markdown.

Results are stored in SQLite + ChromaDB for later semantic search.
Chunking uses Crawl4AI native strategies (SlidingWindow / Regex / Overlapping).
LLM extraction can be enabled per-call or via config.LLM_EXTRACTION_ENABLED.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMConfig,
    LLMExtractionStrategy,
    MemoryAdaptiveDispatcher,
)
from crawl4ai.content_filter_strategy import BM25ContentFilter, PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from fastmcp import Context

from app.config import config
from app.domain import registrable_domain
from app.ratelimit import crawl_with_retry, get_rate_limiter
from app.storage.chroma_store import get_chroma
from app.storage.chunker import chunk_text
from app.storage.sqlite_store import get_store
from app.utils import get_cache_mode, make_id

logger = logging.getLogger(__name__)


def _build_llm_extraction_strategy(query: Optional[str] = None) -> LLMExtractionStrategy:
    """Build an LLMExtractionStrategy using the configured Ollama LLM."""
    return LLMExtractionStrategy(
        llm_config=LLMConfig(provider=config.OLLAMA_LLM_MODEL, api_token=None),
        extraction_type="block",
        instruction=f"Extract information only regarding: {query}" if query else "Extract the key content blocks.",
        chunk_token_threshold=1000,
        overlap_rate=0.1,
        apply_chunking=True,
        input_format="fit_markdown",
        extra_args={"temperature": 0.0, "max_tokens": 800},
    )


# arXiv abstract pages (…/abs/…) are HTML and crawl fine; only the PDF endpoints
# (…/pdf/…) and plain *.pdf URLs need the PDF processor for faithful extraction.
_ARXIV_PDF_RE = re.compile(r"arxiv\.org/pdf/", re.IGNORECASE)


def _is_pdf_url(url: str) -> bool:
    """True when a URL points at a PDF that the browser renderer would mangle.

    The default (browser) crawl of a PDF yields little or no usable markdown.
    Routing these through Crawl4AI's PDF processor recovers the real text.
    """
    try:
        path = urlsplit(url).path.lower()
    except Exception:
        return False
    return path.endswith(".pdf") or bool(_ARXIV_PDF_RE.search(url))


async def _crawl_pdf(url: str) -> Any:
    """Crawl a PDF URL using Crawl4AI's PDF processor (not the browser).

    Produces real extracted text as markdown, fixing the lossy browser path for
    arXiv/PDF sources.
    """
    from crawl4ai.processors.pdf import PDFContentScrapingStrategy, PDFCrawlerStrategy

    run_cfg = CrawlerRunConfig(
        scraping_strategy=PDFContentScrapingStrategy(),
        cache_mode=get_cache_mode(config.CACHE_MODE),
        verbose=False,
    )
    async with AsyncWebCrawler(crawler_strategy=PDFCrawlerStrategy()) as crawler:
        return await crawler.arun(url, config=run_cfg)


def _build_run_config(
    query: Optional[str] = None,
    css_selector: Optional[str] = None,
    excluded_tags: Optional[List[str]] = None,
    cache_mode_str: Optional[str] = None,
    take_screenshot: bool = False,
    wait_for: Optional[str] = None,
    js_code: Optional[str] = None,
    use_llm_extraction: bool = False,
) -> CrawlerRunConfig:
    """Build a CrawlerRunConfig with research-optimised content filtering.

    When use_llm_extraction=True, wires LLMExtractionStrategy into the config
    so Crawl4AI handles chunking+extraction during the crawl itself.
    """
    tags = excluded_tags or ["nav", "footer", "aside", "header", "script", "style"]

    # Pick the filter by query presence: BM25 (query-focused) when a query is
    # given, otherwise Pruning (boilerplate removal).
    if query:
        content_filter = BM25ContentFilter(
            user_query=query,
            bm25_threshold=config.BM25_THRESHOLD,
            language="english",
        )
    else:
        content_filter = PruningContentFilter(
            threshold=config.PRUNING_THRESHOLD,
            threshold_type="dynamic",
            min_word_threshold=config.MIN_WORD_THRESHOLD,
        )
    md_generator = DefaultMarkdownGenerator(content_filter=content_filter)

    # Optional LLM extraction (handles chunking internally)
    extraction_strategy = _build_llm_extraction_strategy(query) if use_llm_extraction else None

    return CrawlerRunConfig(
        markdown_generator=md_generator,
        css_selector=css_selector,
        excluded_tags=tags,
        remove_overlay_elements=True,
        remove_forms=True,
        exclude_external_links=False,
        exclude_social_media_links=True,
        cache_mode=get_cache_mode(cache_mode_str or config.CACHE_MODE),
        page_timeout=config.PAGE_TIMEOUT_MS,
        screenshot=take_screenshot,
        wait_for=wait_for,
        js_code=js_code,
        verbose=False,
        extraction_strategy=extraction_strategy,
    )


def _parse_llm_extracted_chunks(extracted_content: str, url: str, title: str, session_id: Optional[str], query: Optional[str], strategy: str, page_id: str) -> tuple[List[str], List[str], List[Dict]]:
    """Parse result.extracted_content (JSON from LLMExtractionStrategy) into chunk records.

    Returns (chunk_ids, chunk_texts, metadatas) ready for ChromaDB + SQLite.
    """
    try:
        blocks = json.loads(extracted_content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse extracted_content as JSON; skipping LLM chunks.")
        return [], [], []

    if not isinstance(blocks, list):
        blocks = [blocks]

    etld1 = registrable_domain(url)
    chunk_ids: List[str] = []
    chunk_texts: List[str] = []
    metadatas: List[Dict] = []

    for i, block in enumerate(blocks):
        if isinstance(block, dict):
            # Block extraction: join content list into a single string
            content_parts = block.get("content", [])
            if isinstance(content_parts, list):
                text = "\n".join(str(p) for p in content_parts).strip()
            else:
                text = str(content_parts).strip()
        else:
            text = str(block).strip()

        if not text:
            continue

        cid = make_id()
        chunk_ids.append(cid)
        chunk_texts.append(text)
        metadatas.append({
            "url": url,
            "title": title,
            "session_id": session_id or "",
            "query": query or "",
            "chunk_index": str(i),
            "strategy": strategy,
            "page_id": page_id,
            "etld1": etld1,
            "extraction": "llm",
        })

    return chunk_ids, chunk_texts, metadatas


async def _persist_result(
    result: Any,
    session_id: Optional[str],
    strategy: str,
    query: Optional[str],
    total_score: float = 0.0,
) -> str:
    """Save crawl result to SQLite and ChromaDB. Returns page_id.

    Chunking priority:
      1. If result.extracted_content is set → LLM extracted blocks (parsed from JSON).
      2. Otherwise → Crawl4AI native chunking strategy applied to fit_markdown.
    """
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

    store = await get_store()
    await store.save_page(
        page_id=page_id,
        session_id=session_id,
        url=result.url,
        title=title,
        raw_markdown=raw_md,
        fit_markdown=fit_md,
        strategy=strategy,
        total_score=total_score,
        status_code=result.status_code,
        success=result.success,
        error=result.error_message if not result.success else None,
    )

    # Save links
    internal_links = [
        {**lnk, "link_type": "internal"}
        for lnk in (result.links.get("internal", []) if result.links else [])
    ]
    external_links = [
        {**lnk, "link_type": "external"}
        for lnk in (result.links.get("external", []) if result.links else [])
    ]
    await store.save_links(page_id, internal_links + external_links)

    # ── Chunk and store in ChromaDB ──────────────────────────────────────────
    extracted = getattr(result, "extracted_content", None)

    if extracted:
        # Path 1: LLM extraction — parse blocks from result.extracted_content
        chunk_ids, chunk_texts_list, metadatas = _parse_llm_extracted_chunks(
            extracted, result.url, title, session_id, query, strategy, page_id
        )
        if chunk_ids:
            chroma = get_chroma()
            try:
                chroma.add_chunks(chunk_ids, chunk_texts_list, metadatas)
            except Exception:
                pass

            chunk_records = [
                {
                    "id": cid,
                    "chunk_index": int(m["chunk_index"]),
                    "chunk_text": text,
                    "token_count": len(text.split()),
                    "chroma_doc_id": cid,
                    "etld1": m.get("etld1", ""),
                }
                for cid, text, m in zip(chunk_ids, chunk_texts_list, metadatas)
            ]
            await store.save_chunks(page_id, chunk_records)

    elif fit_md.strip():
        # Path 2: Native Crawl4AI chunking strategy applied to fit_markdown
        chunks = chunk_text(fit_md)
        if chunks:
            chroma = get_chroma()
            etld1 = registrable_domain(result.url)
            chunk_ids = [make_id() for _ in chunks]
            c_texts = [c.text for c in chunks]
            metadatas = [
                {
                    "url": result.url,
                    "title": title,
                    "session_id": session_id or "",
                    "query": query or "",
                    "chunk_index": str(c.chunk_index),
                    "strategy": strategy,
                    "page_id": page_id,
                    "etld1": etld1,
                    "extraction": config.CHUNKING_STRATEGY,
                }
                for c in chunks
            ]
            try:
                chroma.add_chunks(chunk_ids, c_texts, metadatas)
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

    return page_id


# Inline content preview length (chars). Full content lives in SQLite + ChromaDB;
# the agent pulls it back on demand via search_chunks(page_id) rather than carrying
# whole pages in its context window.
_PREVIEW_CHARS = 300


def _format_result(result: Any, page_id: str) -> Dict[str, Any]:
    """Format a CrawlResult into the standard tool response dict.

    Returns a short preview (not the full markdown) plus the ``page_id`` so the
    caller can retrieve the complete, semantically-chunked content via
    ``search_chunks``. This keeps per-page payloads small for the agent.
    """
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

    return {
        "success": result.success,
        "url": result.url,
        "page_id": page_id,
        "title": title,
        "fit_preview": fit_md[:_PREVIEW_CHARS],
        "internal_links": [lnk.get("href") for lnk in (result.links or {}).get("internal", [])],
        "external_links": [lnk.get("href") for lnk in (result.links or {}).get("external", [])],
        "metadata": {
            "word_count": len(raw_md.split()),
            "fit_word_count": len(fit_md.split()),
            "status_code": result.status_code,
            "content_stored": bool(fit_md),
        },
        "screenshot_base64": result.screenshot or None,
        "error": result.error_message if not result.success else None,
    }


# ── Tool: crawl_url ──────────────────────────────────────────────────────────

async def crawl_url(
    url: str,
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    css_selector: Optional[str] = None,
    excluded_tags: Optional[List[str]] = None,
    cache_mode: Optional[str] = None,
    take_screenshot: bool = False,
    wait_for: Optional[str] = None,
    js_code: Optional[str] = None,
    use_llm_extraction: Optional[bool] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Crawl a single URL and extract high-quality research content.

    Selects a content filter based on the query:
    - query provided → BM25ContentFilter focuses content on the research topic.
    - no query       → PruningContentFilter removes boilerplate/low-quality blocks.

    Content is chunked using the configured Crawl4AI chunking strategy
    (sliding_window by default) and stored in SQLite + ChromaDB.

    Args:
        url: Target URL to crawl.
        query: Research query for BM25 relevance filtering and storage metadata.
        session_id: Research session identifier for grouping results.
        css_selector: Focus crawl on a specific CSS region (e.g. "main.content").
        excluded_tags: HTML tags to strip (default: nav, footer, aside, header).
        cache_mode: Override cache behaviour: enabled | bypass | disabled.
        take_screenshot: Capture a base64 screenshot of the page.
        wait_for: CSS or JS condition to wait for before extracting.
        js_code: JavaScript to execute after page load.
        use_llm_extraction: Apply LLM extraction for richer chunks (overrides
            config.LLM_EXTRACTION_ENABLED when specified).

    Returns:
        Dict with success, url, title, page_id, fit_preview, links, metadata.
        Full content is stored — retrieve it via search_chunks using the page_id.
    """
    if ctx:
        await ctx.info(f"Crawling: {url}")

    llm_extract = use_llm_extraction if use_llm_extraction is not None else config.LLM_EXTRACTION_ENABLED

    # Per-domain politeness: space out requests to the same eTLD+1.
    await get_rate_limiter().acquire(url)

    if _is_pdf_url(url):
        # PDF/arXiv: route through the PDF processor for faithful text extraction.
        result = await crawl_with_retry(lambda: _crawl_pdf(url), ctx=ctx)
    else:
        browser_cfg = BrowserConfig(headless=True, text_mode=not take_screenshot, light_mode=True)
        run_cfg = _build_run_config(
            query=query,
            css_selector=css_selector,
            excluded_tags=excluded_tags,
            cache_mode_str=cache_mode,
            take_screenshot=take_screenshot,
            wait_for=wait_for,
            js_code=js_code,
            use_llm_extraction=llm_extract,
        )

        async def _do() -> Any:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                return await crawler.arun(url, config=run_cfg)

        result = await crawl_with_retry(_do, ctx=ctx)

    page_id = await _persist_result(result, session_id, "crawl_url", query)

    if ctx:
        status = "✅" if result.success else "❌"
        await ctx.info(f"{status} {url} — page_id: {page_id}")

    return _format_result(result, page_id)


# ── Tool: crawl_many ─────────────────────────────────────────────────────────

async def crawl_many(
    urls: List[str],
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    max_concurrent: int = 5,
    cache_mode: Optional[str] = None,
    use_llm_extraction: Optional[bool] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Crawl multiple URLs concurrently with memory-adaptive dispatch.

    Uses MemoryAdaptiveDispatcher to automatically throttle concurrency when
    system memory exceeds 70%, preventing OOM during large research batches.
    Results are stored incrementally in SQLite + ChromaDB using the configured
    Crawl4AI chunking strategy.

    Args:
        urls: List of URLs to crawl (typically the qualified_urls from triage).
        query: Shared research query for BM25 filtering and storage metadata.
        session_id: Research session identifier for grouping results.
        max_concurrent: Maximum concurrent crawl sessions (default 5).
        cache_mode: Override cache behaviour: enabled | bypass | disabled.
        use_llm_extraction: Apply LLM extraction for richer chunks (overrides
            config.LLM_EXTRACTION_ENABLED when specified).

    Returns:
        Dict with results list, summary stats, and session_id.
    """
    if not urls:
        return {"results": [], "stats": {"total": 0, "success": 0, "failed": 0}, "session_id": session_id}

    if ctx:
        await ctx.info(f"Batch crawling {len(urls)} URLs (max_concurrent={max_concurrent})")

    llm_extract = use_llm_extraction if use_llm_extraction is not None else config.LLM_EXTRACTION_ENABLED

    effective_concurrent = min(max_concurrent, config.MAX_CONCURRENT_CRAWLS)

    browser_cfg = BrowserConfig(headless=True, text_mode=True, light_mode=True)
    run_cfg = _build_run_config(query=query, cache_mode_str=cache_mode, use_llm_extraction=llm_extract)

    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=70.0,
        max_session_permit=effective_concurrent,
    )

    results_out = []
    success_count = 0
    fail_count = 0

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        async for result in await crawler.arun_many(
            urls=urls,
            config=run_cfg.clone(stream=True),
            dispatcher=dispatcher,
        ):
            page_id = await _persist_result(result, session_id, "crawl_many", query)
            formatted = _format_result(result, page_id)
            results_out.append(formatted)

            if result.success:
                success_count += 1
                if ctx:
                    await ctx.info(f"  ✅ {result.url}")
            else:
                fail_count += 1
                if ctx:
                    await ctx.warning(f"  ❌ {result.url}: {result.error_message}")

    if ctx:
        await ctx.info(f"Batch done: {success_count} succeeded, {fail_count} failed")

    return {
        "results": results_out,
        "stats": {
            "total": len(urls),
            "success": success_count,
            "failed": fail_count,
        },
        "session_id": session_id,
    }
