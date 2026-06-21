"""
Tests for the Semantic Scholar citation client (app/citation.py, T2.6).

All network is mocked via ``httpx.MockTransport`` so the suite runs fully
offline. ``asyncio_mode = "auto"`` (pyproject) means ``async def test_*`` needs
no marker.
"""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.citation import (
    CitationClient,
    _parse_publication_date,
    extract_paper_id,
)
from app.config import config


# ── id extraction / normalisation ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        # arXiv: prefixed, urls, pdf path, version stripping
        ("arXiv:2301.00001", "arXiv:2301.00001"),
        ("arxiv:2301.00001", "arXiv:2301.00001"),
        ("https://arxiv.org/abs/2301.00001", "arXiv:2301.00001"),
        ("https://arxiv.org/abs/2301.00001v2", "arXiv:2301.00001"),
        ("https://arxiv.org/pdf/2301.00001", "arXiv:2301.00001"),
        ("arXiv:2301.00001v3", "arXiv:2301.00001"),
        # legacy arXiv scheme
        ("https://arxiv.org/abs/math.GT/0309136", "arXiv:math.GT/0309136"),
        # DOIs (bare + doi.org url + DOI: prefix)
        ("10.1145/3292500.3330701", "DOI:10.1145/3292500.3330701"),
        ("https://doi.org/10.1145/3292500.3330701", "DOI:10.1145/3292500.3330701"),
        ("DOI:10.1145/3292500.3330701", "DOI:10.1145/3292500.3330701"),
        # S2 native ids
        ("0" * 40, "0" * 40),  # 40-hex SHA paperId
        ("CorpusId:215416146", "CorpusId:215416146"),
        ("corpusid:215416146", "CorpusId:215416146"),
    ],
)
def test_extract_paper_id_parses(raw, expected):
    assert extract_paper_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a paper", "https://example.com/blog"])
def test_extract_paper_id_unrecognised_returns_none(raw):
    assert extract_paper_id(raw) is None


# ── publication date parsing ──────────────────────────────────────────────────
def test_parse_publication_date_full():
    assert _parse_publication_date({"publicationDate": "2023-04-15"}) == date(2023, 4, 15)


def test_parse_publication_date_year_fallback():
    assert _parse_publication_date({"publicationDate": None, "year": 2021}) == date(2021, 1, 1)


def test_parse_publication_date_missing_returns_none():
    assert _parse_publication_date({}) is None
    assert _parse_publication_date({"publicationDate": "garbage"}) is None


# ── test helpers: a tiny fake citation graph served via MockTransport ──────────
# Ids use *real* S2 forms so extract_paper_id accepts them: the seed is an arXiv
# id; references/citations are 40-hex SHA paperIds.
# Graph (references direction):  A -> [B, C];  B -> [D];  C -> [];  D -> []
_A = "arXiv:2301.00001"
_B = "b" * 40
_C = "c" * 40
_D = "d" * 40
_Z = "f" * 40  # a citation of A (not followed when walking references)

_GRAPH = {
    _A: {
        "paperId": "a" * 40,
        "title": "Paper A",
        "publicationDate": "2023-01-01",
        "year": 2023,
        "references": [{"paperId": _B}, {"paperId": _C}],
        "citations": [{"paperId": _Z}],
    },
    _B: {
        "paperId": _B,
        "title": "Paper B",
        "publicationDate": "2022-06-01",
        "references": [{"paperId": _D}],
        "citations": [],
    },
    _C: {
        "paperId": _C,
        "title": "Paper C",
        "year": 2020,  # only year → Jan-1 fallback
        "references": [],
        "citations": [],
    },
    _D: {
        "paperId": _D,
        "title": "Paper D",
        "publicationDate": "2019-03-03",
        "references": [],
        "citations": [],
    },
}


def _graph_handler(request: httpx.Request) -> httpx.Response:
    # URL form: {base}/paper/{id}?fields=...  (path is unquoted by httpx)
    paper_id = request.url.path.split("/paper/")[-1]
    record = _GRAPH.get(paper_id)
    if record is None:
        return httpx.Response(404, json={"error": "not found"})
    return httpx.Response(200, json=record)


def _make_client(handler=_graph_handler, *, sleep=None) -> CitationClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    if sleep is None:
        async def sleep(_seconds: float) -> None:  # noqa: ANN001 - test stub
            return None
    return CitationClient(http, sleep=sleep)


# ── metadata mapping ──────────────────────────────────────────────────────────
async def test_get_paper_metadata_maps_fields():
    client = _make_client()
    # a url that normalises to the seed id arXiv:2301.00001
    meta = await client.get_paper_metadata("https://arxiv.org/abs/2301.00001v1")

    assert meta is not None
    assert meta["paper_id"] == _A
    assert meta["s2_paper_id"] == "a" * 40
    assert meta["title"] == "Paper A"
    assert meta["publication_date"] == date(2023, 1, 1)
    assert meta["references"] == [_B, _C]
    assert meta["citations"] == [_Z]
    assert meta["citers"] == meta["citations"]  # alias
    await client.aclose()


async def test_get_paper_metadata_year_only_date():
    client = _make_client()
    meta = await client.get_paper_metadata(_C)
    assert meta is not None
    assert meta["publication_date"] == date(2020, 1, 1)
    await client.aclose()


async def test_get_paper_metadata_invalid_id_returns_none():
    client = _make_client()
    assert await client.get_paper_metadata("just some text") is None
    await client.aclose()


# ── error handling ────────────────────────────────────────────────────────────
async def test_get_paper_metadata_404_returns_none():
    client = _make_client()
    assert await client.get_paper_metadata("arXiv:9999.99999") is None  # not in graph -> 404
    await client.aclose()


async def test_get_paper_metadata_transport_error_returns_none():
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = _make_client(handler=boom)
    assert await client.get_paper_metadata(_A) is None
    await client.aclose()


# ── BFS over the fake graph ───────────────────────────────────────────────────
async def test_bfs_two_hops_walks_references():
    client = _make_client()
    discovered = await client.bfs(_A, direction="references", max_hops=2)

    # Hop0 seed A; hop1 B,C; hop2 D (B->D). Z is a citation, not followed here.
    keys = set(discovered.keys())
    assert keys == {_A, _B, _C, _D}
    # date enrichment propagated through the walk
    assert discovered[_D]["publication_date"] == date(2019, 3, 3)
    await client.aclose()


async def test_bfs_respects_max_hops():
    client = _make_client()
    discovered = await client.bfs(_A, direction="references", max_hops=1)
    # Only seed + immediate references; D (2 hops away) excluded.
    assert set(discovered.keys()) == {_A, _B, _C}
    await client.aclose()


async def test_bfs_seed_404_returns_empty():
    client = _make_client()
    assert await client.bfs("arXiv:9999.99999") == {}  # not in graph -> 404
    await client.aclose()


async def test_bfs_relevance_gate_filters_candidates():
    client = _make_client()

    # Scorer: only "Paper B" clears the bar; C and downstream D are dropped.
    def scorer(context: str, _question: str) -> float:
        return 0.9 if "Paper B" in context else 0.1

    discovered = await client.bfs(
        _A,
        direction="references",
        max_hops=2,
        scorer=scorer,
        relevance_threshold=0.7,
    )
    # A (seed, never gated), B (passes). C filtered at hop1; D unreachable
    # because its only parent B was followed but D itself scores 0.1 -> filtered.
    assert set(discovered.keys()) == {_A, _B}
    await client.aclose()


async def test_bfs_async_scorer_supported():
    client = _make_client()

    async def scorer(_context: str, _question: str) -> float:
        return 1.0  # follow everything

    discovered = await client.bfs(
        _A, direction="references", max_hops=2, scorer=scorer, relevance_threshold=0.7
    )
    assert set(discovered.keys()) == {_A, _B, _C, _D}
    await client.aclose()


# ── rate-delay courtesy ───────────────────────────────────────────────────────
async def test_bfs_sleeps_between_hops():
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    client = _make_client(sleep=fake_sleep)
    await client.bfs(_A, direction="references", max_hops=2)

    # Hop1 (candidates B,C) and hop2 (candidate D) each produce a courtesy delay.
    assert calls == [config.S2_RATE_DELAY_SEC, config.S2_RATE_DELAY_SEC]
    assert all(d >= 1.0 for d in calls)  # courtesy floor (default 1s)
    await client.aclose()


async def test_bfs_no_sleep_when_no_candidates():
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    client = _make_client(sleep=fake_sleep)
    # Seed C has no references -> no next hop -> no delay.
    await client.bfs(_C, direction="references", max_hops=2)
    assert calls == []
    await client.aclose()


# ── API-key header courtesy ───────────────────────────────────────────────────
async def test_api_key_header_sent_when_configured(monkeypatch):
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json=_GRAPH[_A])

    monkeypatch.setattr(config, "S2_API_KEY", "secret-key-123")
    client = _make_client(handler=handler)
    await client.get_paper_metadata(_A)
    assert seen_headers.get("x-api-key") == "secret-key-123"
    await client.aclose()


async def test_no_api_key_header_when_unset(monkeypatch):
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json=_GRAPH[_A])

    monkeypatch.setattr(config, "S2_API_KEY", "")
    client = _make_client(handler=handler)
    await client.get_paper_metadata(_A)
    assert "x-api-key" not in seen_headers
    await client.aclose()


async def test_bfs_direction_validation():
    client = _make_client()
    with pytest.raises(ValueError):
        await client.bfs(_A, direction="sideways")
    await client.aclose()
