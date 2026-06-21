"""
Content-similarity dedup: collapse syndicated / mirrored pages.

Many sources republish the same article (wire stories, blog mirrors, scraper
sites). For research these inflate apparent corroboration. This pass groups
stored pages whose content cosine similarity ≥ config.DEDUP_COSINE_THRESHOLD
into a single canonical page (the earliest crawl) and records the mirrors via
``crawled_pages.duplicate_of``.

Similarity reuses the existing ChromaDB cosine embedding space: each page is
represented by the mean of its stored chunk embeddings, so no re-embedding is
needed.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastmcp import Context

from app.config import config
from app.storage.chroma_store import get_chroma
from app.storage.sqlite_store import get_store


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two equal-length vectors. 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def dedup_pages(
    session_id: Optional[str] = None,
    threshold: Optional[float] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Collapse syndicated / mirrored pages into a single canonical page.

    Compares stored pages pairwise using the cosine similarity of their mean
    chunk embedding. Pages with similarity ≥ threshold are grouped; the earliest
    crawled page in each group is kept as canonical and the rest are recorded as
    mirrors (``duplicate_of`` points at the canonical). Pages already marked as a
    duplicate are skipped, so the pass is idempotent.

    Args:
        session_id: Restrict dedup to one research session (recommended). When
            omitted, all stored pages are considered.
        threshold: Cosine threshold to treat two pages as mirrors
            (default config.DEDUP_COSINE_THRESHOLD).

    Returns:
        Dict with canonical_groups (canonical_page_id → mirror list), counts,
        and the threshold used.
    """
    thr = threshold if threshold is not None else config.DEDUP_COSINE_THRESHOLD

    store = await get_store()
    pages = await store.list_pages_with_chunks(session_id)

    if ctx:
        await ctx.info(f"Dedup: comparing {len(pages)} pages at cosine ≥ {thr}")

    chroma = get_chroma()
    # Compute a representative vector per page (mean of its chunk embeddings).
    vectors: Dict[str, Optional[List[float]]] = {
        p["page_id"]: chroma.get_mean_vector(p["chroma_doc_ids"]) for p in pages
    }

    assigned: set = set()  # page_ids already folded into some group
    groups: List[Dict[str, Any]] = []

    # Pages arrive oldest-first → the first member of each group is canonical.
    for canon in pages:
        cid = canon["page_id"]
        if cid in assigned:
            continue
        cvec = vectors.get(cid)
        mirrors: List[Dict[str, str]] = []
        if cvec is not None:
            for other in pages:
                oid = other["page_id"]
                if oid == cid or oid in assigned:
                    continue
                ovec = vectors.get(oid)
                if ovec is None:
                    continue
                sim = _cosine(cvec, ovec)
                if sim >= thr:
                    assigned.add(oid)
                    await store.mark_duplicate(oid, cid)
                    mirrors.append({"page_id": oid, "url": other["url"], "similarity": round(sim, 4)})
        assigned.add(cid)
        if mirrors:
            groups.append(
                {
                    "canonical_page_id": cid,
                    "canonical_url": canon["url"],
                    "mirrors": mirrors,
                }
            )

    merged_count = sum(len(g["mirrors"]) for g in groups)
    if ctx:
        await ctx.info(f"Dedup done: {merged_count} mirror(s) collapsed into {len(groups)} canonical page(s)")

    return {
        "canonical_groups": groups,
        "stats": {
            "pages_considered": len(pages),
            "duplicate_groups": len(groups),
            "mirrors_merged": merged_count,
            "threshold": thr,
        },
        "session_id": session_id,
    }
