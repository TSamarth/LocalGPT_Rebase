"""
Seeded source corpus for the E5.S2.T1 live acceptance harness.

Each ``make_*`` function returns a pair of ``@node`` stubs:
``(acquirer_stub, extractor_stub, verifier_stub)`` that can be injected via the
``build_research_workflow`` DI seam.  The acquirer stub returns a pre-built
``list[ScoredURL]``; the extractor stub returns ``["page-1"]`` (discarded by the
loop, exactly as in offline tests); the verifier stub returns a pre-built
``ClaimLedger`` that ``enrich_ledger`` then re-enriches deterministically.

The stubs bypass Ollama for the acquire/extract/verify path so the acceptance
tests run against controlled evidence.  The writer may still be real (Ollama) for
AC4/AC5/AC7 to prove the rendered markdown contains the expected sections.
"""
from __future__ import annotations

from datetime import date

from google.adk.workflow import node

from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    ConflictType,
    Contradiction,
    CrawlStrategy,
    ScoredURL,
    SourceRef,
)

# ---------------------------------------------------------------------------
# Shared subtopic id used by every seeded verifier stub so the planner stub
# (which emits subtopic id "s1") and the verifier stubs align.
# ---------------------------------------------------------------------------
_SUBTOPIC_ID = "s1"


# ---------------------------------------------------------------------------
# Acquirer stubs
# ---------------------------------------------------------------------------

def _make_acquirer_stub(urls: list[ScoredURL]):
    """Return a canned acquirer node that yields the supplied URLs."""

    @node
    async def _stub(node_input: str) -> list[ScoredURL]:
        return list(urls)

    return _stub


def _make_extractor_stub():
    """Return a canned extractor node that returns a dummy page-id list."""

    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]

    return _stub


def _make_verifier_stub(ledger: ClaimLedger):
    """Return a canned verifier node that returns ``ledger``."""

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        return ledger

    return _stub


# ---------------------------------------------------------------------------
# AC4 — contradictory sources (two different eTLD+1 domains, FACTUAL conflict)
# ---------------------------------------------------------------------------

AC4_URL_A = ScoredURL(
    url="https://news-alpha.com/tokio-faster",
    score=0.88,
    strategy=CrawlStrategy.CRAWL,
    etld1="news-alpha.com",
    publication_date=date(2023, 6, 1),
)

AC4_URL_B = ScoredURL(
    url="https://facts-beta.org/async-std-faster",
    score=0.85,
    strategy=CrawlStrategy.CRAWL,
    etld1="facts-beta.org",
    publication_date=date(2023, 6, 15),
)

_AC4_CONTRADICTION = Contradiction(
    url=AC4_URL_B.url,
    quote="async-std outperforms Tokio on I/O-bound workloads by 20%",
    conflict_type=ConflictType.FACTUAL,
    confidence_score=0.82,
    publication_date=AC4_URL_B.publication_date,
)

AC4_LEDGER = ClaimLedger(
    claims=[
        Claim(
            id="c1",
            subtopic_id=_SUBTOPIC_ID,
            text="Tokio outperforms async-std on I/O-bound workloads by 15%",
            # enrich_ledger will see two distinct eTLD+1 sources with contradictions →
            # status stays FLAGGED after enrichment
            status=ClaimStatus.FLAGGED,
            sources=[
                SourceRef(url=AC4_URL_A.url, etld1=AC4_URL_A.etld1, quote="Tokio is 15% faster"),
                SourceRef(url=AC4_URL_B.url, etld1=AC4_URL_B.etld1, quote="async-std wins by 20%"),
            ],
            contradictions=[_AC4_CONTRADICTION],
        )
    ]
)


def make_ac4_stubs():
    """Return (acquirer_node, extractor_node, verifier_node) for AC4."""
    return (
        _make_acquirer_stub([AC4_URL_A, AC4_URL_B]),
        _make_extractor_stub(),
        _make_verifier_stub(AC4_LEDGER),
    )


# ---------------------------------------------------------------------------
# AC5 — single-source corpus (UNCORROBORATED)
# ---------------------------------------------------------------------------

AC5_URL = ScoredURL(
    url="https://single-source.io/rust-runtimes",
    score=0.75,
    strategy=CrawlStrategy.CRAWL,
    etld1="single-source.io",
    publication_date=date(2023, 9, 1),
)

AC5_LEDGER = ClaimLedger(
    claims=[
        Claim(
            id="c1",
            subtopic_id=_SUBTOPIC_ID,
            text="Tokio uses a work-stealing thread pool by default",
            status=ClaimStatus.UNCORROBORATED,
            sources=[
                SourceRef(url=AC5_URL.url, etld1=AC5_URL.etld1, quote="work-stealing scheduler"),
            ],
            contradictions=[],
        )
    ]
)


def make_ac5_stubs():
    """Return (acquirer_node, extractor_node, verifier_node) for AC5."""
    return (
        _make_acquirer_stub([AC5_URL]),
        _make_extractor_stub(),
        _make_verifier_stub(AC5_LEDGER),
    )


# ---------------------------------------------------------------------------
# AC7 — temporal drift (old + new source, date delta >= 18 months)
# ---------------------------------------------------------------------------

# 2021-01-01 → 2023-01-01 = 24 months > TEMPORAL_DRIFT_THRESHOLD_MONTHS (18)
AC7_URL_OLD = ScoredURL(
    url="https://old-blog.net/async-std-2021",
    score=0.80,
    strategy=CrawlStrategy.CRAWL,
    etld1="old-blog.net",
    publication_date=date(2021, 1, 1),
)

AC7_URL_NEW = ScoredURL(
    url="https://new-research.org/async-std-2023",
    score=0.83,
    strategy=CrawlStrategy.CRAWL,
    etld1="new-research.org",
    publication_date=date(2023, 1, 1),
)

_AC7_CONTRADICTION = Contradiction(
    url=AC7_URL_NEW.url,
    quote="async-std is now deprecated in favour of Tokio as of 2023",
    conflict_type=ConflictType.TEMPORAL_DRIFT,
    confidence_score=0.79,
    publication_date=AC7_URL_NEW.publication_date,
)

AC7_LEDGER = ClaimLedger(
    claims=[
        Claim(
            id="c1",
            subtopic_id=_SUBTOPIC_ID,
            text="async-std is the recommended async runtime for Rust (as of 2021)",
            status=ClaimStatus.FLAGGED,
            sources=[
                SourceRef(url=AC7_URL_OLD.url, etld1=AC7_URL_OLD.etld1, quote="async-std recommended", publication_date=AC7_URL_OLD.publication_date),
                SourceRef(url=AC7_URL_NEW.url, etld1=AC7_URL_NEW.etld1, quote="deprecated 2023", publication_date=AC7_URL_NEW.publication_date),
            ],
            contradictions=[_AC7_CONTRADICTION],
        )
    ]
)


def make_ac7_stubs():
    """Return (acquirer_node, extractor_node, verifier_node) for AC7."""
    return (
        _make_acquirer_stub([AC7_URL_OLD, AC7_URL_NEW]),
        _make_extractor_stub(),
        _make_verifier_stub(AC7_LEDGER),
    )


# ---------------------------------------------------------------------------
# Generic seeded corpus for AC2/AC3 (minimal kept claim, no contradictions)
# ---------------------------------------------------------------------------

_GENERIC_URL_A = ScoredURL(
    url="https://doc-alpha.dev/tokio",
    score=0.90,
    strategy=CrawlStrategy.CRAWL,
    etld1="doc-alpha.dev",
    publication_date=date(2023, 3, 1),
)

_GENERIC_URL_B = ScoredURL(
    url="https://doc-beta.dev/async-std",
    score=0.88,
    strategy=CrawlStrategy.CRAWL,
    etld1="doc-beta.dev",
    publication_date=date(2023, 3, 15),
)

_GENERIC_LEDGER = ClaimLedger(
    claims=[
        Claim(
            id="c1",
            subtopic_id=_SUBTOPIC_ID,
            text="Tokio is the most widely adopted async runtime in the Rust ecosystem",
            # Two distinct eTLD+1 sources → enrich_ledger will compute KEPT
            status=ClaimStatus.KEPT,
            sources=[
                SourceRef(url=_GENERIC_URL_A.url, etld1=_GENERIC_URL_A.etld1, quote="most popular"),
                SourceRef(url=_GENERIC_URL_B.url, etld1=_GENERIC_URL_B.etld1, quote="widely used"),
            ],
            contradictions=[],
        )
    ]
)


def make_generic_stubs():
    """Return (acquirer_node, extractor_node, verifier_node) for AC2/AC3."""
    return (
        _make_acquirer_stub([_GENERIC_URL_A, _GENERIC_URL_B]),
        _make_extractor_stub(),
        _make_verifier_stub(_GENERIC_LEDGER),
    )
