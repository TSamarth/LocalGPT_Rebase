"""
Artifact schemas — the typed contracts agents hand off to each other.

These ARE the inter-agent API (architecture.md §3). Agents pass these objects
(or their IDs), never raw text dumps, to keep RAM + token use bounded. Every
artifact is JSON-serialisable so the session store can persist/resume it.
"""
from __future__ import annotations

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


# ── Verifier artifacts (FR5) ──────────────────────────────────────────────────
class SourceRef(BaseModel):
    """A single source backing (or contradicting) a claim, with its evidence quote."""
    url: str
    etld1: str = ""
    quote: str = ""


class Claim(BaseModel):
    """One vetted assertion. Traceable to its sources (FR5.3)."""
    id: str
    subtopic_id: str
    text: str
    status: ClaimStatus
    sources: List[SourceRef] = Field(default_factory=list)
    contradictions: List[SourceRef] = Field(default_factory=list)


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