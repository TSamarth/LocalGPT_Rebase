"""
Artifact schemas — the typed contracts agents hand off to each other.

These ARE the inter-agent API (architecture.md §3). Agents pass these objects
(or their IDs), never raw text dumps, to keep RAM + token use bounded. Every
artifact is JSON-serialisable so the session store can persist/resume it.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────
class Depth(str, Enum):
    """Adaptive research depth, chosen by the Planner from query complexity."""
    SHALLOW = "shallow"
    NORMAL = "normal"
    DEEP = "deep"


class SourceClass(str, Enum):
    """Where a subtopic's evidence should come from."""
    WEB = "web"
    ACADEMIC = "academic"
    CODE = "code"
    SEED = "seed"  # user-provided URLs/files (FR3.2)


class CrawlStrategy(str, Enum):
    """Crawl strategy assigned by the MCP triage heuristic.

    Mirrors the crawl4ai MCP tool names so the Acquirer/Extractor handoff
    maps 1:1 onto the MCP tools.
    """
    ADAPTIVE = "adaptive_crawl"
    DEEP = "deep_crawl"
    CRAWL = "crawl_url"
    SKIP = "skip"


class ClaimStatus(str, Enum):
    """Verifier verdict for a single claim (FR5)."""
    KEPT = "kept"                    # corroborated by >=2 independent sources
    FLAGGED = "flagged"              # independent sources contradict each other
    UNCORROBORATED = "uncorroborated"  # only one source


class ConflictType(str, Enum):
    """Classification of a flagged contradiction (FR5.5, architecture.md §12.1)."""
    FACTUAL = "factual"                # genuine disagreement on fact
    METHODOLOGICAL = "methodological"  # differ due to method/data, not fact
    TEMPORAL_DRIFT = "temporal_drift"  # field evolved over time, not a real conflict (FR5.6)


class TemporalStatus(str, Enum):
    """Recency label on a claim when temporal drift is detected (FR5.6, §12.2)."""
    CURRENT = "current"      # backed by the newer source
    DATED = "dated"          # backed by the older source
    UNCERTAIN = "uncertain"  # publication_date missing → cannot classify


class Stage(str, Enum):
    """Position in the orchestrator stage machine (architecture.md §4)."""
    INTAKE = "intake"
    CLARIFY = "clarify"
    PLAN = "plan"
    RESEARCH = "research"
    SYNTHESIZE = "synthesize"
    WRITE = "write"
    DONE = "done"


# ── Planner artifacts (FR2) ───────────────────────────────────────────────────
class Subtopic(BaseModel):
    """One aspect of the query the report must cover."""
    id: str
    question: str
    target_evidence: int = Field(
        ge=1,
        description="Corroborated-claim count that marks this subtopic done (drives stop-rule).",
    )
    source_classes: List[SourceClass] = Field(default_factory=lambda: [SourceClass.WEB])


class ResearchPlan(BaseModel):
    """Output of the Planner; approved/edited by the user at Checkpoint 1."""
    subtopics: List[Subtopic]
    depth: Depth = Depth.NORMAL
    seed_urls: List[str] = Field(default_factory=list)  # forced into crawl set (US5)
    est_iterations: int = Field(default=1, ge=1)


# ── Acquirer artifact (FR3) ───────────────────────────────────────────────────
class ScoredURL(BaseModel):
    """A triaged candidate URL from discover_urls + score_and_triage_urls."""
    url: str
    score: float
    strategy: CrawlStrategy
    source: str = ""                       # discovery source that found it
    also_in: List[str] = Field(default_factory=list)  # other sources (cross-source signal)
    etld1: str = ""                        # registrable domain — used for independence test
    # ── T0.4 enrichment (citation BFS / date metadata, architecture.md §3.4, §11) ──
    publication_date: Optional[date] = None  # from Semantic Scholar/arXiv metadata; feeds temporal drift
    citation_refs: List[str] = Field(default_factory=list)  # paper IDs followed during citation BFS


# ── Verifier artifacts (FR5) ──────────────────────────────────────────────────
class SourceRef(BaseModel):
    """A single source backing a claim, with its evidence quote."""
    url: str
    etld1: str = ""
    quote: str = ""
    publication_date: Optional[date] = None  # T0.4: propagated from chunk metadata for temporal drift


class Contradiction(BaseModel):
    """A source that contradicts a claim, scored by the Verifier (FR5.5, §12.1).

    Superset of SourceRef fields plus contradiction scoring, so legacy
    contradiction JSON (url/etld1/quote only) still validates against defaults.
    """
    url: str
    etld1: str = ""
    quote: str = ""
    publication_date: Optional[date] = None
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)  # 0–1, three-tier rubric
    conflict_type: ConflictType = ConflictType.FACTUAL


class Claim(BaseModel):
    """One vetted assertion. Traceable to its sources (FR5.3)."""
    id: str
    subtopic_id: str
    text: str
    status: ClaimStatus
    temporal_status: Optional[TemporalStatus] = None  # T0.4: set only on temporal-drift claims (FR5.6)
    sources: List[SourceRef] = Field(default_factory=list)
    contradictions: List[Contradiction] = Field(default_factory=list)


class ClaimLedger(BaseModel):
    """All claims gathered across the run; the Writer's sole input for content."""
    claims: List[Claim] = Field(default_factory=list)

    def for_subtopic(self, subtopic_id: str) -> List[Claim]:
        return [c for c in self.claims if c.subtopic_id == subtopic_id]

    def kept_count(self, subtopic_id: str) -> int:
        """Corroborated-claim count for a subtopic — compared against target_evidence."""
        return sum(
            1 for c in self.claims
            if c.subtopic_id == subtopic_id and c.status == ClaimStatus.KEPT
        )


# ── Clarifier artifact (FR1) ──────────────────────────────────────────────────
class ClarifyResult(BaseModel):
    """Clarifier verdict. needs_input pauses the run for user questions (before start only)."""
    status: str  # "clear" | "needs_input"
    normalized_query: str
    questions: List[str] = Field(default_factory=list)


# ── Session / resume state (architecture.md §5) ───────────────────────────────
class StageState(BaseModel):
    """Lightweight run pointer persisted as stage.json. Plan/ledger/draft live in
    their own files; this only tracks WHERE the run is so it can resume."""
    session_id: str
    raw_query: str
    normalized_query: str = ""
    stage: Stage = Stage.INTAKE
    current_subtopic_id: Optional[str] = None
    iteration: int = 0
    updated_at: str = ""  # ISO8601, set by the session store on save
