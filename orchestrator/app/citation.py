"""
Semantic Scholar citation-graph client (architecture.md §3.4, §11; task T2.6).

A plain ``httpx`` async utility — **not** an MCP tool. The Acquirer agent calls
it directly for ``source_class == academic`` AND ``plan.depth == "deep"`` sources
to walk the citation graph (2-hop BFS) and enrich ``ScoredURL`` with
``publication_date`` and ``citation_refs``.

Design notes
------------
* The Semantic Scholar Graph API (v1) accepts a paper id in several forms via a
  single ``/paper/{id}`` endpoint: a bare 40-char ``paperId``/SHA, or a prefixed
  external id such as ``arXiv:2301.00001``, ``DOI:10.1145/...``, ``CorpusId:...``.
  :func:`extract_paper_id` normalises arbitrary urls/ids onto one of those forms.
* LLM relevance scoring (the gate in §11) lives in the Acquirer — it owns the
  resident model. This module keeps that as an *injectable* callback so the BFS
  is deterministic and testable; with no scorer it simply follows everything.
* Network failures on a single paper degrade gracefully (return ``None`` / skip)
  rather than aborting a whole BFS.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Awaitable, Callable, Iterable, Optional

import httpx

from app.config import config

# Fields requested from the S2 Graph API. ``publicationDate`` is ISO ``YYYY-MM-DD``
# (may be null); ``year`` is the coarse fallback when only the year is known.
_PAPER_FIELDS = (
    "title,publicationDate,year,externalIds,"
    "references.paperId,citations.paperId"
)

# A discovered candidate paper, returned by :meth:`CitationClient.bfs`.
# ``RelevanceScorer`` takes a candidate's textual context (title/abstract) and the
# subtopic question, returning a 0–1 relevance score. May be sync or async.
RelevanceScorer = Callable[[str, str], "float | Awaitable[float]"]

# Matches an arXiv id with optional version suffix, in either the modern
# (``2301.00001``) or legacy (``math.GT/0309136``) scheme.
_ARXIV_NEW = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
_ARXIV_OLD = re.compile(r"\b([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
_S2_SHA = re.compile(r"^[0-9a-f]{40}$")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)\b", re.IGNORECASE)


def extract_paper_id(raw: str) -> Optional[str]:
    """Normalise a url or id into a Semantic Scholar ``/paper/{id}`` query token.

    Returns ``None`` when nothing recognisable is found (caller skips the paper).

    Examples
    --------
    ``arXiv:2301.00001``            -> ``arXiv:2301.00001``
    ``https://arxiv.org/abs/2301.00001v2`` -> ``arXiv:2301.00001``
    ``https://arxiv.org/pdf/2301.00001`` -> ``arXiv:2301.00001``
    ``10.1145/3292500.3330701``     -> ``DOI:10.1145/3292500.3330701``
    ``<40-hex>``                    -> ``<40-hex>`` (native S2 paperId)
    ``CorpusId:215416146``          -> ``CorpusId:215416146`` (passthrough)
    """
    if not raw:
        return None
    s = raw.strip()

    # Already-prefixed external ids (arXiv:/DOI:/CorpusId:/MAG:/ACL:/PMID:/PMCID:).
    m = re.match(r"^(arXiv|DOI|CorpusId|MAG|ACL|PMID|PMCID):", s, re.IGNORECASE)
    if m:
        prefix = m.group(1)
        rest = s[m.end():].strip()
        if prefix.lower() == "arxiv":
            rest = _strip_arxiv_version(rest)
            return f"arXiv:{rest}"
        # Canonicalise the well-known prefixes to their documented casing.
        canon = {
            "doi": "DOI",
            "corpusid": "CorpusId",
            "mag": "MAG",
            "acl": "ACL",
            "pmid": "PMID",
            "pmcid": "PMCID",
        }[prefix.lower()]
        return f"{canon}:{rest}"

    # Bare 40-char S2 SHA paperId.
    if _S2_SHA.match(s):
        return s

    # arXiv id embedded in a url or bare (new then legacy scheme).
    am = _ARXIV_NEW.search(s)
    if am:
        return f"arXiv:{am.group(1)}"
    am = _ARXIV_OLD.search(s)
    if am:
        return f"arXiv:{am.group(1)}"

    # DOI embedded in a url (e.g. https://doi.org/10.1145/...) or bare.
    dm = _DOI.search(s)
    if dm:
        return f"DOI:{dm.group(1).rstrip('.')}"

    return None


def _strip_arxiv_version(arxiv_id: str) -> str:
    """Drop a trailing ``vN`` version so ids resolve to the canonical paper."""
    return re.sub(r"v\d+$", "", arxiv_id.strip())


def _parse_publication_date(data: dict) -> Optional[date]:
    """Best-effort ``date`` from an S2 paper record.

    Prefers ``publicationDate`` (``YYYY-MM-DD``); falls back to ``year`` →
    ``Jan 1`` of that year; returns ``None`` when neither is usable.
    """
    raw = data.get("publicationDate")
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    year = data.get("year")
    if isinstance(year, int):
        try:
            return date(year, 1, 1)
        except ValueError:
            return None
    return None


def _ids_from(records: Optional[Iterable[dict]]) -> list[str]:
    """Pull non-empty ``paperId`` values out of a references/citations list."""
    out: list[str] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        pid = rec.get("paperId")
        if pid:
            out.append(pid)
    return out


class CitationClient:
    """Async client over the Semantic Scholar Graph API.

    Parameters
    ----------
    http_client:
        Inject an :class:`httpx.AsyncClient` (e.g. backed by
        ``httpx.MockTransport``) to make the client fully testable offline. When
        omitted, one is created on first use and closed by :meth:`aclose` /
        context-manager exit.
    sleep:
        Injectable coroutine used for the inter-hop courtesy delay (patchable in
        tests to assert it fires without real waiting). Defaults to
        :func:`asyncio.sleep`.
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "CitationClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"}
        if config.S2_API_KEY:
            headers["x-api-key"] = config.S2_API_KEY
        return headers

    # ── single-paper fetch ────────────────────────────────────────────────────
    async def get_paper_metadata(self, paper_id: str) -> Optional[dict]:
        """Fetch metadata for one paper, normalising the id first.

        Returns a dict::

            {
                "paper_id":         <normalised query id>,
                "s2_paper_id":      <S2 paperId or None>,
                "title":            <str>,
                "publication_date": <datetime.date | None>,
                "references":       [<paperId>, ...],   # papers this one cites
                "citations":        [<paperId>, ...],   # papers that cite this one
                "citers":           [<paperId>, ...],   # alias of "citations"
            }

        Returns ``None`` on 404, missing id, or any transport/HTTP error — a
        single bad paper never aborts a wider BFS.
        """
        normalized = extract_paper_id(paper_id)
        if not normalized:
            return None

        url = f"{config.S2_API_BASE_URL.rstrip('/')}/paper/{normalized}"
        try:
            resp = await self._get_client().get(
                url, params={"fields": _PAPER_FIELDS}, headers=self._headers()
            )
        except httpx.HTTPError:
            return None

        if resp.status_code != httpx.codes.OK:
            return None
        try:
            data = resp.json()
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None

        references = _ids_from(data.get("references"))
        citations = _ids_from(data.get("citations"))
        return {
            "paper_id": normalized,
            "s2_paper_id": data.get("paperId"),
            "title": data.get("title") or "",
            "publication_date": _parse_publication_date(data),
            "references": references,
            "citations": citations,
            "citers": citations,  # alias — same list, friendlier name
        }

    # ── citation BFS (§11) ────────────────────────────────────────────────────
    async def bfs(
        self,
        seed_paper_id: str,
        *,
        subtopic_question: str = "",
        direction: str = "references",
        max_hops: Optional[int] = None,
        scorer: Optional[RelevanceScorer] = None,
        relevance_threshold: Optional[float] = None,
    ) -> dict[str, dict]:
        """Breadth-first walk of the citation graph from ``seed_paper_id``.

        Walks ``direction`` (``"references"`` = papers cited by the seed, or
        ``"citations"`` = papers citing the seed) up to ``max_hops``
        (default :data:`config.CITATION_BFS_HOPS`). The seed's own metadata is
        always included; discovered papers are fetched one hop at a time.

        A courtesy delay of :data:`config.S2_RATE_DELAY_SEC` is awaited *between
        hops* (not before the first, not after the last) to respect rate limits.

        ``scorer`` is the optional relevance gate (§11). When supplied, a
        candidate is only enqueued if ``scorer(context, subtopic_question) >=
        relevance_threshold`` (default :data:`config.CITATION_RELEVANCE_THRESHOLD`).
        With no scorer every neighbour is followed (keeps the walk testable).

        Returns ``{paper_id: metadata}`` for every paper reached (seed included),
        keyed by the metadata's normalised ``paper_id``.
        """
        if direction not in ("references", "citations"):
            raise ValueError("direction must be 'references' or 'citations'")
        hops = config.CITATION_BFS_HOPS if max_hops is None else max_hops
        threshold = (
            config.CITATION_RELEVANCE_THRESHOLD
            if relevance_threshold is None
            else relevance_threshold
        )

        seed_meta = await self.get_paper_metadata(seed_paper_id)
        if seed_meta is None:
            return {}

        discovered: dict[str, dict] = {seed_meta["paper_id"]: seed_meta}
        frontier: list[dict] = [seed_meta]

        for _hop in range(hops):
            # Collect the next-hop candidate ids from the current frontier.
            candidate_ids: list[str] = []
            seen_this_hop: set[str] = set()
            for meta in frontier:
                for cand_id in meta.get(direction, []):
                    if cand_id in discovered or cand_id in seen_this_hop:
                        continue
                    seen_this_hop.add(cand_id)
                    candidate_ids.append(cand_id)

            if not candidate_ids:
                break

            # Courtesy delay before issuing the next hop's API calls.
            await self._sleep(config.S2_RATE_DELAY_SEC)

            next_frontier: list[dict] = []
            for cand_id in candidate_ids:
                cand_meta = await self.get_paper_metadata(cand_id)
                if cand_meta is None:
                    continue
                key = cand_meta["paper_id"]
                if key in discovered:
                    continue

                if scorer is not None:
                    context = cand_meta.get("title", "")
                    score = scorer(context, subtopic_question)
                    if isinstance(score, Awaitable):
                        score = await score
                    if score < threshold:
                        continue

                discovered[key] = cand_meta
                next_frontier.append(cand_meta)

            frontier = next_frontier
            if not frontier:
                break

        return discovered


# Module-level convenience wrappers — let the Acquirer call without managing a
# client instance for one-off lookups.
async def get_paper_metadata(paper_id: str) -> Optional[dict]:
    """One-shot metadata fetch (creates and closes a client internally)."""
    async with CitationClient() as client:
        return await client.get_paper_metadata(paper_id)


async def citation_bfs(
    seed_paper_id: str,
    *,
    subtopic_question: str = "",
    direction: str = "references",
    max_hops: Optional[int] = None,
    scorer: Optional[RelevanceScorer] = None,
    relevance_threshold: Optional[float] = None,
) -> dict[str, dict]:
    """One-shot 2-hop citation BFS (creates and closes a client internally)."""
    async with CitationClient() as client:
        return await client.bfs(
            seed_paper_id,
            subtopic_question=subtopic_question,
            direction=direction,
            max_hops=max_hops,
            scorer=scorer,
            relevance_threshold=relevance_threshold,
        )
