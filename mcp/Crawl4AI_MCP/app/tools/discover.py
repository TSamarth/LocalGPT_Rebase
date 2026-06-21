"""
Multi-source query → URL discovery.

`discover_urls` is the front of the research pipeline: it turns a free-text
query into a de-duplicated list of candidate URLs by fanning out to several
search sources concurrently. It does NOT crawl — the agent should next call
`score_and_triage_urls` (or pass `triage=True`) before crawling.

Sources (each adapter is independently guarded so one failure never kills the
others):
  - duckduckgo       : general web (ddgs library, no key)
  - arxiv            : academic preprints (arXiv Atom API, no key)
  - semantic_scholar : academic papers (Semantic Scholar API, optional key)
  - serpapi          : Google via serpapi.com (requires SERPAPI_KEY, else skipped)
  - google_serp      : Crawl4AI native GoogleSearchCrawler (opt-in, fragile)
"""
from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastmcp import Context

from app.config import config
from app.tools.triage import score_and_triage_urls

# Sources that need no key / flag and are safe to run by default.
_ALL_SOURCES = ["duckduckgo", "arxiv", "semantic_scholar", "serpapi", "google_serp"]

# Query params stripped during URL canonicalization (tracking noise).
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"ref", "fbclid", "gclid", "mc_cid", "mc_eid"}

_HTTP_TIMEOUT = 15.0
_USER_AGENT = "Crawl4AI-MCP/0.1 (research discovery)"


# ---------------------------------------------------------------------------
# URL canonicalization / de-duplication
# ---------------------------------------------------------------------------

def _canonicalize(url: str) -> str:
    """Normalize a URL for de-dup: lowercase scheme+host, strip default ports,
    trailing slash, fragment, and common tracking query params."""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return url.strip()

    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    # Drop default ports
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    path = parts.path.rstrip("/") or "/"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


def _hit(url: str, title: str = "", snippet: str = "", source: str = "") -> Dict[str, str]:
    return {"url": url, "title": title or "", "snippet": snippet or "", "source": source}


# ---------------------------------------------------------------------------
# Source adapters — each returns List[hit] or raises (caller catches)
# ---------------------------------------------------------------------------

async def _search_duckduckgo(query: str, n: int) -> List[Dict[str, str]]:
    """DuckDuckGo web search via the synchronous `ddgs` library, off-thread."""
    from ddgs import DDGS

    def _run() -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=n):
                href = r.get("href") or r.get("url") or ""
                if href:
                    out.append(_hit(href, r.get("title", ""), r.get("body", ""), "duckduckgo"))
        return out

    return await asyncio.to_thread(_run)


async def _search_arxiv(query: str, n: int) -> List[Dict[str, str]]:
    """arXiv Atom API. Returns the abstract page URL for each result."""
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": n,
    }
    url = "https://export.arxiv.org/api/query?" + urlencode(params)
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(text)
    out: List[Dict[str, str]] = []
    for entry in root.findall("atom:entry", ns):
        link_el = entry.find("atom:id", ns)
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        href = (link_el.text or "").strip() if link_el is not None else ""
        if href:
            out.append(_hit(
                href,
                (title_el.text or "").strip() if title_el is not None else "",
                (summary_el.text or "").strip() if summary_el is not None else "",
                "arxiv",
            ))
    return out


async def _search_semantic_scholar(query: str, n: int) -> List[Dict[str, str]]:
    """Semantic Scholar paper search. Prefers open-access PDF URL when present."""
    params = {
        "query": query,
        "limit": min(n, 100),
        "fields": "title,url,abstract,openAccessPdf",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode(params)
    headers = {"User-Agent": _USER_AGENT}
    if config.SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    out: List[Dict[str, str]] = []
    for paper in data.get("data", []) or []:
        oa = paper.get("openAccessPdf") or {}
        href = oa.get("url") or paper.get("url") or ""
        if href:
            out.append(_hit(href, paper.get("title", ""), paper.get("abstract", "") or "", "semantic_scholar"))
    return out


async def _search_serpapi(query: str, n: int) -> List[Dict[str, str]]:
    """Google results via serpapi.com. Skipped (empty) when no key configured."""
    if not config.SERPAPI_KEY:
        return []
    params = {
        "engine": "google",
        "q": query,
        "num": n,
        "api_key": config.SERPAPI_KEY,
    }
    url = "https://serpapi.com/search?" + urlencode(params)
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    out: List[Dict[str, str]] = []
    for item in data.get("organic_results", []) or []:
        href = item.get("link") or ""
        if href:
            out.append(_hit(href, item.get("title", ""), item.get("snippet", "") or "", "serpapi"))
    return out


async def _search_google_serp(query: str, n: int) -> List[Dict[str, str]]:
    """Crawl4AI native Google SERP scraping. Opt-in via GOOGLE_SERP_ENABLED.

    Heavyweight/fragile: launches a browser and performs one-time LLM schema
    generation. Returns organic-result links parsed from its JSON output.
    """
    if not config.GOOGLE_SERP_ENABLED:
        return []
    from crawl4ai.crawlers.google_search.crawler import GoogleSearchCrawler

    raw = await GoogleSearchCrawler().run(query=query, search_type="text")
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"GoogleSearchCrawler error: {payload['error']}")

    out: List[Dict[str, str]] = []
    # The crawler returns a dict keyed by schema name; organic results carry links.
    organic = []
    if isinstance(payload, dict):
        organic = payload.get("organic_schema") or payload.get("organic") or []
        if isinstance(organic, str):
            try:
                organic = json.loads(organic)
            except Exception:
                organic = []
    for item in organic or []:
        href = (item or {}).get("link") or ""
        if href:
            out.append(_hit(href, item.get("title", ""), item.get("snippet", "") or "", "google_serp"))
        if len(out) >= n:
            break
    return out


_ADAPTERS = {
    "duckduckgo": _search_duckduckgo,
    "arxiv": _search_arxiv,
    "semantic_scholar": _search_semantic_scholar,
    "serpapi": _search_serpapi,
    "google_serp": _search_google_serp,
}


def _resolve_sources(sources: Optional[List[str]]) -> List[str]:
    if sources:
        requested = [s.strip().lower() for s in sources if s and s.strip()]
    else:
        requested = [s.strip().lower() for s in config.DISCOVER_DEFAULT_SOURCES.split(",") if s.strip()]
    # Keep only known sources, preserve order, drop dupes.
    seen: set = set()
    resolved: List[str] = []
    for s in requested:
        if s in _ADAPTERS and s not in seen:
            seen.add(s)
            resolved.append(s)
    return resolved


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

async def discover_urls(
    query: str,
    sources: Optional[List[str]] = None,
    max_results_per_source: Optional[int] = None,
    max_total: Optional[int] = None,
    triage: bool = False,
    score_threshold: float = 0.3,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Discover candidate URLs for a research query across multiple search sources.

    Fans out to the selected sources concurrently, merges and de-duplicates the
    results, and returns a unified URL list. This is the FIRST step of the
    research pipeline — follow it with `score_and_triage_urls` (or set
    `triage=True`) before crawling so low-quality URLs are filtered out.

    Args:
        query: Free-text research query.
        sources: Subset of ["duckduckgo", "arxiv", "semantic_scholar",
            "serpapi", "google_serp"]. Defaults to config.DISCOVER_DEFAULT_SOURCES.
            Note: "serpapi" needs SERPAPI_KEY and "google_serp" needs
            GOOGLE_SERP_ENABLED, otherwise they yield nothing.
        max_results_per_source: Cap per source (default config value).
        max_total: Overall cap after de-dup (default config value).
        triage: When True, immediately run score_and_triage_urls on the
            discovered URLs and return the triage result (for agents that may
            otherwise skip the triage step).
        score_threshold: Passed to triage when triage=True.

    Returns (triage=False):
        {
          "urls": [{"url", "title", "snippet", "source", "also_in": [...]}, ...],
          "by_source": {source: count},
          "errors": {source: message},
          "stats": {"total": int, "sources_used": [...]},
          "recommended_next_step": "..."
        }

    Returns (triage=True):
        The score_and_triage_urls result plus a "discovery" metadata block.
    """
    n_per = max_results_per_source or config.DISCOVER_MAX_RESULTS_PER_SOURCE
    cap = max_total or config.DISCOVER_MAX_TOTAL
    used = _resolve_sources(sources)

    if ctx:
        await ctx.info(f"Discovering URLs for '{query}' across sources: {', '.join(used) or '(none)'}")

    errors: Dict[str, str] = {}
    by_source: Dict[str, int] = {}
    merged: Dict[str, Dict[str, Any]] = {}  # canonical_url -> hit

    if used:
        results = await asyncio.gather(
            *(_ADAPTERS[s](query, n_per) for s in used),
            return_exceptions=True,
        )
        for source, res in zip(used, results):
            if isinstance(res, BaseException):
                errors[source] = f"{type(res).__name__}: {res}"
                if ctx:
                    await ctx.info(f"  source '{source}' failed: {errors[source]}")
                continue
            count = 0
            for hit in res:
                canon = _canonicalize(hit["url"])
                if canon in merged:
                    also = merged[canon].setdefault("also_in", [])
                    if hit["source"] not in also and hit["source"] != merged[canon]["source"]:
                        also.append(hit["source"])
                    continue
                entry = dict(hit)
                entry["url"] = canon
                entry.setdefault("also_in", [])
                merged[canon] = entry
                count += 1
            by_source[source] = count

    urls = list(merged.values())[:cap]

    if ctx:
        await ctx.info(f"Discovered {len(urls)} unique URLs ({len(errors)} source error(s))")

    if triage:
        triage_result = await score_and_triage_urls(
            urls=[u["url"] for u in urls],
            query=query,
            score_threshold=score_threshold,
            ctx=ctx,
        )
        triage_result["discovery"] = {
            "by_source": by_source,
            "errors": errors,
            "sources_used": used,
            "discovered_total": len(urls),
        }
        return triage_result

    return {
        "urls": urls,
        "by_source": by_source,
        "errors": errors,
        "stats": {"total": len(urls), "sources_used": used},
        "recommended_next_step": (
            "Call score_and_triage_urls with these urls and the same query to "
            "filter out low-quality sources before crawling."
        ),
    }
