"""
Verifier agent (Story 3.6, FR5/FR5.5/FR5.6) — the *trust core* of the pipeline.

The Verifier turns retrieved chunks into a vetted ``ClaimLedger``. The split of
responsibility mirrors the rest of the pipeline (architecture.md §9): the **model**
owns *content* — which chunks support or contradict each candidate claim, and a
0–1 methodological-explicitness signal per source — while **deterministic Python**
owns *policy*: independence, claim status, contradiction confidence and temporal
drift. Keeping policy model-free makes the trust decisions reproducible, config-
driven, and unit-testable without a running Ollama (architecture.md §3.6, §12).

Four pure policy functions encode the spec and are tested in isolation:

* :func:`are_independent` / :func:`count_independent_sources` — the independence
  test (Open Q2): different eTLD+1 AND content cosine < ``INDEPENDENCE_COSINE_THRESHOLD``.
* :func:`classify_claim_status` — kept (≥2 independent) / flagged (independent
  sources contradict) / uncorroborated (single source).
* :func:`score_contradiction` — the three-tier additive confidence rubric (§12.1).
* :func:`classify_conflict` — temporal-drift classification + ``temporal_status``
  tagging (§12.2, AC7).

Model invocation is injectable: :func:`verify` takes a ``runner`` callable so
tests exercise the full parse → enrich path against canned JSON. The deterministic
enrichment is ALWAYS re-applied to the parsed ledger (:func:`enrich_ledger`) so the
final ``ClaimLedger`` is policy-consistent regardless of what the LLM emitted.

The Verifier is reasoning-only by default (operates on artifacts), but MAY hold
the MCP ``search_chunks`` READ tool (architecture.md §2). When a tool is attached
ADK forbids ``output_schema`` (``build_agent`` enforces this), so the factory
drops the schema in that case; with no tool it uses ``output_schema=ClaimLedger``.
The optional toolset is injectable and lazily imported, exactly like the Extractor.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple, Union

from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from ..config import config
from ..jsonio import invoke_json_with_retry, loads_first_json
from ..llm import build_agent
from ..schemas import (
    ClaimLedger,
    ClaimStatus,
    ConflictType,
    Contradiction,
    SourceRef,
    TemporalStatus,
)

# A runner takes the verifier prompt/payload and returns the model's structured
# output, either as a raw JSON string or an already-parsed mapping. Async so the
# default (ADK Runner over Ollama) and test doubles share one signature.
VerifierRunner = Callable[[str], Awaitable[Union[str, dict]]]

#: state/output key the agent writes its ``ClaimLedger`` JSON under.
OUTPUT_KEY = "claim_ledger"

#: Agent name (also used as the ADK app name for the default runner).
AGENT_NAME = "verifier"

#: MCP read tool the Verifier is permitted to hold (architecture.md §2).
SEARCH_TOOL_NAME = "search_chunks"

__all__ = [
    "ROLE_PROMPT",
    "OUTPUT_KEY",
    "AGENT_NAME",
    "VerifierRunner",
    "are_independent",
    "count_independent_sources",
    "classify_claim_status",
    "month_delta",
    "score_contradiction",
    "classify_conflict",
    "enrich_claim",
    "enrich_ledger",
    "build_verifier",
    "parse_ledger",
    "verify",
]


ROLE_PROMPT = """\
You are the VERIFIER in a local deep-research pipeline — the trust core. Your input
is a set of retrieved text chunks for one subtopic, each carrying its source URL,
registrable domain (etld1), an evidence quote, and an optional publication_date.

Your job is to judge CONTENT, not policy:
1. Extract the candidate factual CLAIMS the chunks make about the subtopic. Keep
   each claim a single, checkable assertion; give it a short stable id ("c1", ...).
2. For each claim, map every chunk that SUPPORTS it into `sources`, and every chunk
   that CONTRADICTS it into `contradictions`, each with its url, etld1, quote, and
   publication_date (copy these through unchanged — never invent a date).
3. Quote faithfully from the chunks; do not paraphrase away the disagreement. When
   sources disagree, retain ALL sides — do not silently pick a winner.

Do NOT decide a claim's final status, confidence_score, or conflict_type yourself —
deterministic policy downstream computes those from source independence, recency,
and the methodological signal. You only surface the evidence faithfully.

Return ONLY a JSON object conforming to this schema (no prose, no markdown):

{
  "claims": [
    {
      "id": "c1",
      "subtopic_id": "s1",
      "text": "the checkable assertion",
      "status": "uncorroborated",
      "sources": [
        {"url": "https://a.example", "etld1": "a.example",
         "quote": "verbatim supporting text", "publication_date": "2023-01-15"}
      ],
      "contradictions": [
        {"url": "https://b.example", "etld1": "b.example",
         "quote": "verbatim contradicting text", "publication_date": "2020-01-15"}
      ]
    }
  ]
}

Leave `status` as a placeholder ("uncorroborated"); it will be recomputed. Output
the JSON object and nothing else.
"""


# ── Tier 1 / claim status: source independence (Open Q2) ──────────────────────
def are_independent(source_a: SourceRef, source_b: SourceRef, cosine: float) -> bool:
    """Two sources are independent iff DIFFERENT etld1 AND content cosine below threshold.

    Encodes the independence test (architecture.md §3.6 Open Q2, §12.1 tier 1):
    same registrable domain → not independent (same publisher); near-duplicate
    content (``cosine >= config.INDEPENDENCE_COSINE_THRESHOLD``) → not independent
    (syndication / mirror). Pure and model-free.

    ``cosine`` is the content cosine similarity between the two sources' chunks,
    supplied by the caller (the MCP/Chroma layer). A missing/unknown similarity
    should be passed as ``0.0`` (treated as maximally distinct on that axis).
    """
    if not source_a.etld1 or not source_b.etld1:
        # Without a domain we cannot prove same-publisher; fall back to the
        # content-similarity axis alone (conservative: only the cosine gate applies).
        return cosine < config.INDEPENDENCE_COSINE_THRESHOLD
    if source_a.etld1 == source_b.etld1:
        return False
    return cosine < config.INDEPENDENCE_COSINE_THRESHOLD


def count_independent_sources(
    sources: Sequence[SourceRef],
    cosine_lookup: Optional[Callable[[SourceRef, SourceRef], float]] = None,
) -> int:
    """Greedy count of mutually-independent sources in a list.

    A source is counted as independent when its etld1 is DISTINCT from every
    already-counted source AND its content cosine against each of them is below
    ``config.INDEPENDENCE_COSINE_THRESHOLD`` (i.e. :func:`are_independent` holds
    pairwise against the accepted set). Simple, documented and order-stable; this
    is the corroboration count the stop-rule and claim status rely on.

    ``cosine_lookup(a, b)`` supplies pairwise content similarity; when omitted the
    similarity is treated as ``0.0`` (distinct), so independence reduces to the
    etld1 test alone.
    """
    def _cos(a: SourceRef, b: SourceRef) -> float:
        return cosine_lookup(a, b) if cosine_lookup is not None else 0.0

    accepted: List[SourceRef] = []
    for src in sources:
        if all(are_independent(src, kept, _cos(src, kept)) for kept in accepted):
            accepted.append(src)
    return len(accepted)


def classify_claim_status(
    sources: Sequence[SourceRef],
    contradictions: Sequence[Contradiction],
    cosine_lookup: Optional[Callable[[SourceRef, SourceRef], float]] = None,
) -> ClaimStatus:
    """Assign a claim's verdict from its evidence (FR5.1/FR5.2, AC5).

    Policy:
      * FLAGGED — independent sources contradict each other: at least one
        contradiction that is independent of at least one supporting source
        (retain all sides downstream).
      * KEPT — corroborated by >= 2 independent supporting sources.
      * UNCORROBORATED — otherwise (single / non-independent support).

    Contradiction takes precedence over corroboration: a claim disputed by an
    independent source is FLAGGED even if otherwise well-supported, so the
    disagreement is never hidden.
    """
    def _cos(a: SourceRef, b: SourceRef) -> float:
        return cosine_lookup(a, b) if cosine_lookup is not None else 0.0

    # A contradiction is a SourceRef superset, so it plugs straight into are_independent.
    for contra in contradictions:
        contra_ref = SourceRef(
            url=contra.url, etld1=contra.etld1, quote=contra.quote,
            publication_date=contra.publication_date,
        )
        if any(are_independent(contra_ref, src, _cos(contra_ref, src)) for src in sources):
            return ClaimStatus.FLAGGED

    if count_independent_sources(sources, cosine_lookup) >= 2:
        return ClaimStatus.KEPT
    return ClaimStatus.UNCORROBORATED


# ── Tier 2 helper: month delta between publication dates ──────────────────────
def month_delta(a: date, b: date) -> int:
    """Whole-month gap between two dates, order-independent.

    Computed as ``abs(year*12 + month)`` difference (calendar months) — stdlib only,
    no extra dependency. Day-of-month is ignored, which is the right granularity for
    the 18-month temporal-drift threshold (architecture.md §12.2): we care about how
    many months apart two publications are, not exact day counts.
    """
    return abs((a.year * 12 + a.month) - (b.year * 12 + b.month))


# ── Tier scoring: three-tier additive contradiction confidence (§12.1) ────────
def score_contradiction(
    source: SourceRef,
    contradiction: Contradiction,
    cosine: float,
    methodological_explicitness: float,
) -> float:
    """Three-tier additive confidence in ``[0, 1]`` that a contradiction is real (FR5.5).

    Each tier contributes 0–0.33 (architecture.md §12.1); the sum is clamped to
    ``[0, 1]``:

    * **Tier 1 — source independence**: different etld1 AND low cosine → full 0.33;
      same etld1 → 0.0 (same publisher, not a real cross-source conflict). When
      domains differ, the tier scales linearly with how far cosine sits below the
      independence threshold (more distinct content → more confident it is a genuine
      independent contradiction).
    * **Tier 2 — recency delta**: both sources recent (gap < 12 months) → full 0.33
      (a live, current disagreement). The score decays as the gap widens; a large
      gap signals temporal drift rather than a factual conflict, so it contributes
      little. Missing dates → neutral 0.0 (cannot reason about recency).
    * **Tier 3 — methodological explicitness**: a 0–1 signal the LLM supplies per
      source (cites data/methods → higher) scaled into 0–0.33. Injected so this
      function stays pure and testable.

    ``cosine`` is the content similarity between ``source`` and ``contradiction``;
    ``methodological_explicitness`` is the LLM's 0–1 judgement.
    """
    tier_max = 1.0 / 3.0

    # Tier 1: source independence.
    if source.etld1 and contradiction.etld1 and source.etld1 == contradiction.etld1:
        tier1 = 0.0
    else:
        threshold = config.INDEPENDENCE_COSINE_THRESHOLD
        if threshold <= 0:
            distinctness = 1.0
        else:
            distinctness = max(0.0, min(1.0, (threshold - cosine) / threshold))
        tier1 = tier_max * distinctness

    # Tier 2: recency delta.
    if source.publication_date is None or contradiction.publication_date is None:
        tier2 = 0.0
    else:
        gap = month_delta(source.publication_date, contradiction.publication_date)
        # Full weight when both are within a 12-month window; linear decay to 0 as
        # the gap approaches the temporal-drift threshold (drift, not live conflict).
        drift = max(1, config.TEMPORAL_DRIFT_THRESHOLD_MONTHS)
        if gap < 12:
            tier2 = tier_max
        elif gap >= drift:
            tier2 = 0.0
        else:
            tier2 = tier_max * (1.0 - (gap - 12) / (drift - 12)) if drift > 12 else 0.0

    # Tier 3: methodological explicitness (clamped LLM signal).
    tier3 = tier_max * max(0.0, min(1.0, methodological_explicitness))

    return max(0.0, min(1.0, tier1 + tier2 + tier3))


# ── Temporal drift classification (§12.2, FR5.6, AC7) ─────────────────────────
def classify_conflict(
    source: SourceRef,
    contradiction: Contradiction,
    *,
    methodological: bool = False,
) -> Tuple[ConflictType, Optional[TemporalStatus]]:
    """Classify a contradiction's type and the parent claim's ``temporal_status``.

    Returns ``(conflict_type, temporal_status)`` where ``temporal_status`` is the
    label for the claim as backed by ``source`` (the supporting side):

    * Both dates present AND month gap >= ``config.TEMPORAL_DRIFT_THRESHOLD_MONTHS``
      → ``TEMPORAL_DRIFT``. The OLDER side is ``DATED``, the NEWER is ``CURRENT``;
      ``source`` is labelled accordingly (architecture.md §12.2).
    * Either date missing → ``UNCERTAIN`` temporal_status and the conflict is NOT
      temporal drift: it falls back to methodological/factual (the §12.2 fallback).
    * Small gap (< threshold) → factual or methodological (per ``methodological``),
      with no temporal label (``None``).

    ``methodological`` is the LLM's content judgement of whether the disagreement
    stems from differing method/data rather than fact; it only steers the non-drift
    fallback so this function stays pure.
    """
    fallback = ConflictType.METHODOLOGICAL if methodological else ConflictType.FACTUAL

    if source.publication_date is None or contradiction.publication_date is None:
        # Cannot reason about recency → never classify as drift (§12.2 fallback).
        return fallback, TemporalStatus.UNCERTAIN

    gap = month_delta(source.publication_date, contradiction.publication_date)
    if gap >= config.TEMPORAL_DRIFT_THRESHOLD_MONTHS:
        # The supporting source is CURRENT if it is the newer of the two, else DATED.
        status = (
            TemporalStatus.CURRENT
            if source.publication_date >= contradiction.publication_date
            else TemporalStatus.DATED
        )
        return ConflictType.TEMPORAL_DRIFT, status

    return fallback, None


# ── Deterministic enrichment of a parsed ledger ───────────────────────────────
def enrich_claim(
    claim,
    *,
    cosine_lookup: Optional[Callable[[SourceRef, SourceRef], float]] = None,
    contradiction_cosine: Optional[Callable[[SourceRef, Contradiction], float]] = None,
    methodological_explicitness: Optional[Callable[[Contradiction], float]] = None,
    is_methodological: Optional[Callable[[Contradiction], bool]] = None,
):
    """Apply all policy to ONE claim, returning a NEW, policy-consistent ``Claim``.

    Recomputes ``status`` from independence, and for every contradiction recomputes
    ``confidence_score`` (three-tier rubric) and ``conflict_type`` (with temporal
    drift). The claim's ``temporal_status`` is set to the first temporal-drift label
    found (or UNCERTAIN when a contradiction lacks a date); ``None`` when no
    contradiction carries temporal information. The input claim is left untouched.

    The callables are injectable signal providers (the LLM/Chroma layer supplies
    them); all default to neutral values so the function is fully testable offline.
    """
    def _ccos(s: SourceRef, c: Contradiction) -> float:
        return contradiction_cosine(s, c) if contradiction_cosine is not None else 0.0

    def _meth(c: Contradiction) -> float:
        return methodological_explicitness(c) if methodological_explicitness is not None else 0.0

    def _is_meth(c: Contradiction) -> bool:
        return is_methodological(c) if is_methodological is not None else False

    updated = claim.model_copy(deep=True)
    updated.status = classify_claim_status(
        updated.sources, updated.contradictions, cosine_lookup
    )

    # Representative supporting source for date/independence comparisons (the
    # first source backing the claim). Absent → no date-based reasoning is possible.
    primary = updated.sources[0] if updated.sources else None
    temporal_status: Optional[TemporalStatus] = None

    for contra in updated.contradictions:
        ref = primary or SourceRef(url="", etld1="", quote="")
        contra.confidence_score = score_contradiction(
            ref, contra, _ccos(primary, contra) if primary else 0.0, _meth(contra)
        )
        conflict_type, t_status = classify_conflict(
            ref, contra, methodological=_is_meth(contra)
        )
        contra.conflict_type = conflict_type
        if t_status is not None and temporal_status is None:
            temporal_status = t_status
        # A confirmed drift label wins over a tentative UNCERTAIN.
        if t_status == TemporalStatus.UNCERTAIN and temporal_status is None:
            temporal_status = TemporalStatus.UNCERTAIN
        elif t_status in (TemporalStatus.CURRENT, TemporalStatus.DATED):
            temporal_status = t_status

    updated.temporal_status = temporal_status
    return updated


def enrich_ledger(
    ledger: ClaimLedger,
    *,
    cosine_lookup: Optional[Callable[[SourceRef, SourceRef], float]] = None,
    contradiction_cosine: Optional[Callable[[SourceRef, Contradiction], float]] = None,
    methodological_explicitness: Optional[Callable[[Contradiction], float]] = None,
    is_methodological: Optional[Callable[[Contradiction], bool]] = None,
) -> ClaimLedger:
    """Apply deterministic policy to EVERY claim, returning a new ``ClaimLedger``.

    Pure (the input ledger is not mutated). This is what makes the final ledger
    policy-consistent no matter what the LLM emitted for status/confidence/type.
    """
    return ClaimLedger(
        claims=[
            enrich_claim(
                claim,
                cosine_lookup=cosine_lookup,
                contradiction_cosine=contradiction_cosine,
                methodological_explicitness=methodological_explicitness,
                is_methodological=is_methodological,
            )
            for claim in ledger.claims
        ]
    )


# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_ledger(raw: Union[str, dict, ClaimLedger]) -> ClaimLedger:
    """Validate raw model output into a ``ClaimLedger``.

    Accepts a JSON string, a mapping, or an already-built ``ClaimLedger`` (so
    callers can hand back whatever the runner produced). Legacy contradiction JSON
    (url/etld1/quote only) validates against the schema defaults, so older outputs
    still load cleanly. Deterministic enrichment is applied separately by
    :func:`verify`, keeping this function a pure validation boundary.
    """
    if isinstance(raw, ClaimLedger):
        return raw
    if isinstance(raw, str):
        return ClaimLedger.model_validate(loads_first_json(raw))
    return ClaimLedger.model_validate(raw)


# ── Agent factory ─────────────────────────────────────────────────────────────
def _build_default_toolset() -> BaseToolset:
    """Construct the real crawl4ai ``MCPToolset`` (stdio subprocess), READ tool only.

    Imported lazily because ``mcp`` is an optional ADK extra: keeping the import
    out of module scope lets the orchestrator (and the offline unit tests) load
    this module — and inject a fake toolset — without ``mcp`` installed.

    ``tool_filter`` restricts the agent to ``search_chunks`` so the live Verifier
    can retrieve crawled chunks itself, per subtopic, without holding any write
    tool (least-privilege, architecture.md §1/§2).
    """
    from google.adk.tools.mcp_tool import MCPToolset, StdioConnectionParams
    from mcp import StdioServerParameters

    server_cwd_path = Path(config.MCP_SERVER_CWD).resolve()
    server_cwd = str(server_cwd_path)
    venv_python = str(server_cwd_path / ".venv" / "Scripts" / "python.exe")
    return MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=venv_python,
                args=["main.py"],
                cwd=server_cwd,
            ),
            timeout=500.0,
        ),
        tool_filter=[SEARCH_TOOL_NAME],
    )


def build_verifier(
    *,
    toolset: Optional[BaseToolset] = None,
    model: Optional[object] = None,
    output_key: str = OUTPUT_KEY,
) -> LlmAgent:
    """Construct the Verifier agent.

    Two modes (architecture.md §2, §3.6):

    * **Reasoning-only** (default, ``toolset=None``): no tools, so it uses
      ``output_schema=ClaimLedger`` for structured JSON — the cheapest, most
      offline-testable shape, matching Planner/Writer.
    * **With ``search_chunks``**: when a READ toolset is injected the agent holds
      it; ADK forbids combining an output schema with tools (``build_agent``
      enforces this), so ``output_schema`` is dropped in that case.

    ``toolset`` is injectable (tests pass a fake ``BaseToolset``); ``model`` is
    injectable so tests can bypass constructing a real ``LiteLlm``.
    """
    if toolset is not None:
        return build_agent(
            name=AGENT_NAME,
            role_prompt=ROLE_PROMPT,
            tools=[toolset],
            output_key=output_key,
            model=model,
        )
    return build_agent(
        name=AGENT_NAME,
        role_prompt=ROLE_PROMPT,
        output_schema=ClaimLedger,
        output_key=output_key,
        model=model,
    )


# ── Default runner + injectable async entry ───────────────────────────────────
async def _default_runner(payload: str) -> str:
    """Drive the real Verifier agent through ADK over the local Ollama model.

    Built lazily and only used when no ``runner`` is injected, so importing this
    module (and unit-testing it) never requires a running Ollama. Returns the raw
    JSON text the agent emitted under ``OUTPUT_KEY``.
    """
    import json

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = build_verifier()
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=AGENT_NAME, user_id="orchestrator", session_id="verify"
    )
    runner = Runner(agent=agent, app_name=AGENT_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=payload)])
    final_text = ""
    for event in runner.run(
        user_id="orchestrator", session_id="verify", new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)

    session = await session_service.get_session(
        app_name=AGENT_NAME, user_id="orchestrator", session_id="verify"
    )
    if session is not None:
        stored = session.state.get(OUTPUT_KEY)
        if isinstance(stored, dict):
            return json.dumps(stored)
        if isinstance(stored, str) and stored.strip():
            return stored
    return final_text


async def verify(
    payload: str,
    *,
    runner: Optional[VerifierRunner] = None,
    cosine_lookup: Optional[Callable[[SourceRef, SourceRef], float]] = None,
    contradiction_cosine: Optional[Callable[[SourceRef, Contradiction], float]] = None,
    methodological_explicitness: Optional[Callable[[Contradiction], float]] = None,
    is_methodological: Optional[Callable[[Contradiction], bool]] = None,
) -> ClaimLedger:
    """Produce a policy-consistent ``ClaimLedger`` from a verifier payload.

    The model extracts claims + maps supporting/contradicting chunks; this function
    then ALWAYS re-applies the deterministic policy (:func:`enrich_ledger`) so the
    returned ledger's status / confidence_score / conflict_type / temporal_status
    are correct regardless of what the LLM emitted.

    ``runner`` is injectable: pass an async callable ``(payload) -> str | dict`` to
    run offline against canned output (unit tests, replay). The signal callables let
    the caller feed real cosine/methodology evidence; they default to neutral values.
    """
    invoke = runner or _default_runner
    raw = await invoke_json_with_retry(invoke, payload)
    try:
        parsed = parse_ledger(raw)
    except ValueError:
        # No parseable JSON even after the JSON-only re-ask (e.g. search/crawl tools
        # timed out and the model answered in prose). Degrade to an empty ledger so
        # the run survives rather than crashing the trust core (E0.S2).
        parsed = ClaimLedger(claims=[])
    return enrich_ledger(
        parsed,
        cosine_lookup=cosine_lookup,
        contradiction_cosine=contradiction_cosine,
        methodological_explicitness=methodological_explicitness,
        is_methodological=is_methodological,
    )
