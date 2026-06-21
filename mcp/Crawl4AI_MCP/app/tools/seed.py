"""
Seed-URL / local-file ingest.

`ingest_seeds` forces user-supplied sources into the research corpus, bypassing
discovery and triage entirely. Two source kinds:

  - urls  : crawled with the normal crawl pipeline (crawl_url), so they land in
            SQLite + ChromaDB exactly like any other crawled page.
  - files : local text/markdown files read from disk and persisted directly
            (no browser). For safety, file paths must resolve inside
            config.SEED_INGEST_DIR.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import Context

from app.config import config
from app.domain import registrable_domain
from app.storage.chroma_store import get_chroma
from app.storage.chunker import chunk_text
from app.storage.sqlite_store import get_store
from app.tools.crawl import crawl_url
from app.utils import make_id


def _resolve_seed_path(file_ref: str) -> Optional[Path]:
    """Resolve a seed file reference under SEED_INGEST_DIR.

    Returns the resolved path, or None if it escapes the seed dir or is missing.
    """
    base = Path(config.SEED_INGEST_DIR).resolve()
    candidate = Path(file_ref)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except Exception:
        return None
    if base != resolved and base not in resolved.parents:
        return None  # path traversal outside the seed dir
    if not resolved.is_file():
        return None
    return resolved


async def _ingest_file(path: Path, query: Optional[str], session_id: Optional[str]) -> Dict[str, Any]:
    """Read a local file as text and persist it as a page + chunks."""
    text = path.read_text(encoding="utf-8", errors="replace")
    page_id = make_id()
    url = path.as_uri()
    title = path.name
    etld1 = registrable_domain(url)  # "" for file:// URLs — expected

    store = await get_store()
    await store.save_page(
        page_id=page_id,
        session_id=session_id,
        url=url,
        title=title,
        raw_markdown=text,
        fit_markdown=text,
        strategy="seed_file",
        total_score=0.0,
        status_code=200,
        success=True,
    )

    chunk_count = 0
    if text.strip():
        chunks = chunk_text(text)
        if chunks:
            chroma = get_chroma()
            chunk_ids = [make_id() for _ in chunks]
            metadatas = [
                {
                    "url": url,
                    "title": title,
                    "session_id": session_id or "",
                    "query": query or "",
                    "chunk_index": str(c.chunk_index),
                    "strategy": "seed_file",
                    "page_id": page_id,
                    "etld1": etld1,
                    "extraction": config.CHUNKING_STRATEGY,
                }
                for c in chunks
            ]
            try:
                chroma.add_chunks(chunk_ids, [c.text for c in chunks], metadatas)
            except Exception:
                pass
            await store.save_chunks(
                page_id,
                [
                    {
                        "id": cid,
                        "chunk_index": c.chunk_index,
                        "chunk_text": c.text,
                        "token_count": c.token_count,
                        "chroma_doc_id": cid,
                        "etld1": etld1,
                    }
                    for cid, c in zip(chunk_ids, chunks)
                ],
            )
            chunk_count = len(chunks)

    return {"source": str(path), "page_id": page_id, "url": url, "chunks": chunk_count, "success": True}


async def ingest_seeds(
    urls: Optional[List[str]] = None,
    files: Optional[List[str]] = None,
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Force user-provided URLs and local files into the research corpus.

    Bypasses discovery/triage: every supplied URL is crawled and every supplied
    file is read and stored, so they are guaranteed to appear in storage and in
    subsequent search_chunks results.

    Args:
        urls: Seed URLs to crawl (each via crawl_url with the given query).
        files: Local file paths (absolute, or relative to config.SEED_INGEST_DIR).
            Paths that escape SEED_INGEST_DIR or don't exist are reported as errors.
        query: Optional research query passed through to crawling/storage metadata.
        session_id: Research session identifier for grouping.

    Returns:
        Dict with per-source results, an errors map, and summary stats.
    """
    urls = urls or []
    files = files or []

    if ctx:
        await ctx.info(f"Ingesting {len(urls)} seed URL(s) and {len(files)} seed file(s)")

    results: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}

    for url in urls:
        try:
            res = await crawl_url(url=url, query=query, session_id=session_id, ctx=ctx)
            results.append(
                {
                    "source": url,
                    "kind": "url",
                    "page_id": res.get("page_id"),
                    "url": res.get("url"),
                    "success": res.get("success", False),
                }
            )
        except Exception as e:
            errors[url] = f"{type(e).__name__}: {e}"

    for file_ref in files:
        path = _resolve_seed_path(file_ref)
        if path is None:
            errors[file_ref] = (
                f"file not found or outside SEED_INGEST_DIR ({config.SEED_INGEST_DIR})"
            )
            continue
        try:
            res = await _ingest_file(path, query, session_id)
            res["kind"] = "file"
            results.append(res)
        except Exception as e:
            errors[file_ref] = f"{type(e).__name__}: {e}"

    success = sum(1 for r in results if r.get("success"))
    return {
        "results": results,
        "errors": errors,
        "stats": {
            "requested": len(urls) + len(files),
            "ingested": len(results),
            "succeeded": success,
            "failed": len(errors),
        },
        "session_id": session_id,
    }
