"""
Verifier agent tests (T3.6, FR5/FR5.5/FR5.6). All offline — the model is never
invoked: deterministic policy functions are checked directly, and ``verify`` is
driven with an injected async runner returning canned JSON. No Ollama, no network.

Covers: the independence test (same-domain rejected, mirror-by-cosine rejected,
genuine independent accepted), claim-status assignment (kept >=2 independent,
uncorroborated single, flagged on contradiction), confidence-score bounds + tier
behaviour, temporal-drift classification (>=18mo -> TEMPORAL_DRIFT + dated/current,
missing date -> UNCERTAIN + not drift, small delta -> factual), ``parse_ledger``
roundtrip + legacy JSON, and ``verify`` end-to-end through an injected runner.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.base_toolset import BaseToolset

from app.agents.verifier import (
    OUTPUT_KEY,
    are_independent,
    build_verifier,
    classify_claim_status,
    classify_conflict,
    count_independent_sources,
    enrich_ledger,
    month_delta,
    parse_ledger,
    score_contradiction,
    verify,
)
from app.config import config
from app.schemas import (
    ClaimLedger,
    ClaimStatus,
    ConflictType,
    Contradiction,
    SourceRef,
    TemporalStatus,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _src(etld1: str, *, url: str | None = None, pub: date | None = None) -> SourceRef:
    return SourceRef(url=url or f"https://{etld1}/p", etld1=etld1, quote="q", publication_date=pub)


def _contra(etld1: str, *, url: str | None = None, pub: date | None = None) -> Contradiction:
    return Contradiction(url=url or f"https://{etld1}/c", etld1=etld1, quote="c", publication_date=pub)


def _runner_returning(payload):
    async def _run(_payload: str):
        return payload

    return _run


# ── build_verifier ────────────────────────────────────────────────────────────
class _FakeToolset(BaseToolset):
    async def get_tools(self, readonly_context=None):  # noqa: D401 - test stub
        return []


def test_build_verifier_reasoning_only_uses_schema_no_tools():
    agent = build_verifier()
    assert isinstance(agent, LlmAgent)
    assert agent.output_schema is ClaimLedger
    assert agent.output_key == OUTPUT_KEY
    assert not agent.tools
    assert agent.instruction.strip()


def test_build_verifier_with_tool_drops_schema():
    """With a search tool attached, ADK forbids output_schema — it must be dropped."""
    agent = build_verifier(toolset=_FakeToolset())
    assert agent.tools, "verifier must hold the injected search toolset"
    assert agent.output_schema is None


def test_build_verifier_accepts_injected_model_without_ollama():
    agent = build_verifier(model="stub-model")
    assert agent.model == "stub-model"


# ── independence test (Open Q2, §3.6) ─────────────────────────────────────────
def test_independent_when_different_domain_and_low_cosine():
    assert are_independent(_src("a.com"), _src("b.com"), cosine=0.10) is True


def test_not_independent_same_domain():
    # Same etld1 → same publisher, never independent regardless of cosine.
    assert are_independent(_src("a.com"), _src("a.com"), cosine=0.0) is False


def test_not_independent_when_mirror_by_cosine():
    # Different domains but near-duplicate content (syndication/mirror) → rejected.
    high = config.INDEPENDENCE_COSINE_THRESHOLD + 0.01
    assert are_independent(_src("a.com"), _src("b.com"), cosine=high) is False


def test_independence_threshold_is_strict_less_than():
    at = config.INDEPENDENCE_COSINE_THRESHOLD
    assert are_independent(_src("a.com"), _src("b.com"), cosine=at) is False
    assert are_independent(_src("a.com"), _src("b.com"), cosine=at - 0.001) is True


def test_count_independent_sources_collapses_same_domain_and_mirrors():
    high = config.INDEPENDENCE_COSINE_THRESHOLD + 0.01
    sources = [_src("a.com", url="https://a.com/1"),
               _src("a.com", url="https://a.com/2"),  # same domain → not counted
               _src("b.com"),
               _src("c.com")]

    # Default lookup (cosine 0.0): a.com counted once, plus b, c → 3 independent.
    assert count_independent_sources(sources) == 3

    # Now make b.com a content mirror of a.com → b dropped, leaving a + c = 2.
    def _cos(x: SourceRef, y: SourceRef) -> float:
        domains = {x.etld1, y.etld1}
        return high if domains == {"a.com", "b.com"} else 0.0

    assert count_independent_sources(sources, _cos) == 2


# ── claim status assignment (FR5.1/FR5.2, AC5) ────────────────────────────────
def test_status_kept_with_two_independent_sources():
    status = classify_claim_status([_src("a.com"), _src("b.com")], [])
    assert status == ClaimStatus.KEPT


def test_status_uncorroborated_single_source():
    assert classify_claim_status([_src("a.com")], []) == ClaimStatus.UNCORROBORATED


def test_status_uncorroborated_when_two_sources_share_domain():
    # Two chunks from the same publisher are NOT two independent sources.
    sources = [_src("a.com", url="https://a.com/1"), _src("a.com", url="https://a.com/2")]
    assert classify_claim_status(sources, []) == ClaimStatus.UNCORROBORATED


def test_status_flagged_on_independent_contradiction():
    sources = [_src("a.com"), _src("b.com")]
    contradictions = [_contra("z.com")]  # independent of the supporting sources
    assert classify_claim_status(sources, contradictions) == ClaimStatus.FLAGGED


def test_same_domain_contradiction_does_not_flag():
    # A "contradiction" from the same publisher as the only source isn't independent.
    sources = [_src("a.com")]
    contradictions = [_contra("a.com")]
    assert classify_claim_status(sources, contradictions) == ClaimStatus.UNCORROBORATED


# ── contradiction confidence scoring (§12.1, FR5.5) ───────────────────────────
def test_confidence_score_within_bounds():
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("b.com", pub=date(2023, 2, 1))
    score = score_contradiction(s, c, cosine=0.1, methodological_explicitness=1.0)
    assert 0.0 <= score <= 1.0


def test_confidence_max_all_tiers_high():
    """Independent + both recent + fully methodological → near the 1.0 ceiling."""
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("b.com", pub=date(2023, 3, 1))  # 2-month gap < 12mo
    score = score_contradiction(s, c, cosine=0.0, methodological_explicitness=1.0)
    assert score == pytest.approx(1.0, abs=1e-9)


def test_confidence_tier1_zero_when_same_domain():
    """Same-domain → tier 1 contributes nothing; remaining tiers cap at ~0.667."""
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("a.com", pub=date(2023, 2, 1))
    score = score_contradiction(s, c, cosine=0.0, methodological_explicitness=1.0)
    assert score == pytest.approx(2.0 / 3.0, abs=1e-9)


def test_confidence_methodological_signal_increases_score():
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("b.com", pub=date(2023, 2, 1))
    low = score_contradiction(s, c, cosine=0.1, methodological_explicitness=0.0)
    high = score_contradiction(s, c, cosine=0.1, methodological_explicitness=1.0)
    assert high > low


def test_confidence_recency_decays_with_large_gap():
    """A large date gap (drift territory) yields a lower recency contribution."""
    s = _src("a.com", pub=date(2023, 1, 1))
    near = score_contradiction(s, _contra("b.com", pub=date(2023, 3, 1)),
                               cosine=0.1, methodological_explicitness=0.0)
    far = score_contradiction(s, _contra("b.com", pub=date(2018, 1, 1)),
                              cosine=0.1, methodological_explicitness=0.0)
    assert near > far


def test_confidence_missing_dates_neutral_recency():
    s = _src("a.com", pub=None)
    c = _contra("b.com", pub=None)
    # Only tier1 (independence) contributes; tier2/tier3 are 0 → ~0.33.
    score = score_contradiction(s, c, cosine=0.0, methodological_explicitness=0.0)
    assert score == pytest.approx(1.0 / 3.0, abs=1e-9)


# ── month_delta helper ────────────────────────────────────────────────────────
def test_month_delta_is_order_independent_and_calendar_based():
    assert month_delta(date(2020, 1, 1), date(2021, 7, 1)) == 18
    assert month_delta(date(2021, 7, 1), date(2020, 1, 1)) == 18
    assert month_delta(date(2020, 1, 31), date(2020, 1, 1)) == 0  # day ignored


# ── temporal drift classification (§12.2, FR5.6, AC7) ─────────────────────────
def test_temporal_drift_when_gap_at_threshold_newer_is_current():
    older = _src("a.com", pub=date(2020, 1, 1))
    newer_contra = _contra("b.com", pub=date(2021, 7, 1))  # 18mo gap == threshold
    ctype, tstatus = classify_conflict(older, newer_contra)
    assert ctype == ConflictType.TEMPORAL_DRIFT
    # The supporting source is OLDER → it is DATED.
    assert tstatus == TemporalStatus.DATED


def test_temporal_drift_supporting_source_newer_is_current():
    newer = _src("a.com", pub=date(2022, 6, 1))
    older_contra = _contra("b.com", pub=date(2020, 1, 1))  # > 18mo
    ctype, tstatus = classify_conflict(newer, older_contra)
    assert ctype == ConflictType.TEMPORAL_DRIFT
    assert tstatus == TemporalStatus.CURRENT


def test_small_delta_is_factual_not_drift():
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("b.com", pub=date(2023, 6, 1))  # 5 months < threshold
    ctype, tstatus = classify_conflict(s, c)
    assert ctype == ConflictType.FACTUAL
    assert tstatus is None


def test_small_delta_methodological_when_flagged():
    s = _src("a.com", pub=date(2023, 1, 1))
    c = _contra("b.com", pub=date(2023, 6, 1))
    ctype, tstatus = classify_conflict(s, c, methodological=True)
    assert ctype == ConflictType.METHODOLOGICAL
    assert tstatus is None


def test_missing_date_yields_uncertain_and_not_drift():
    s = _src("a.com", pub=None)
    c = _contra("b.com", pub=date(2020, 1, 1))
    ctype, tstatus = classify_conflict(s, c)
    assert ctype == ConflictType.FACTUAL  # fallback, never temporal_drift
    assert tstatus == TemporalStatus.UNCERTAIN


# ── parse_ledger ──────────────────────────────────────────────────────────────
def _ledger_json() -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "id": "c1",
                    "subtopic_id": "s1",
                    "text": "X is true.",
                    "status": "uncorroborated",
                    "sources": [
                        {"url": "https://a.com/p", "etld1": "a.com", "quote": "X",
                         "publication_date": "2023-01-01"},
                        {"url": "https://b.com/p", "etld1": "b.com", "quote": "X too"},
                    ],
                    "contradictions": [],
                }
            ]
        }
    )


def test_parse_ledger_accepts_str_dict_and_model():
    from_str = parse_ledger(_ledger_json())
    from_dict = parse_ledger(json.loads(_ledger_json()))
    from_model = parse_ledger(ClaimLedger.model_validate_json(_ledger_json()))
    for led in (from_str, from_dict, from_model):
        assert isinstance(led, ClaimLedger)
        assert led.claims[0].id == "c1"
        assert led.claims[0].sources[0].publication_date == date(2023, 1, 1)


def test_parse_ledger_legacy_contradiction_json_uses_defaults():
    """Legacy contradiction JSON (url/etld1/quote only) validates against defaults."""
    legacy = json.dumps(
        {
            "claims": [
                {
                    "id": "c1",
                    "subtopic_id": "s1",
                    "text": "X",
                    "status": "flagged",
                    "sources": [{"url": "https://a.com", "etld1": "a.com"}],
                    "contradictions": [{"url": "https://b.com", "etld1": "b.com", "quote": "no"}],
                }
            ]
        }
    )
    led = parse_ledger(legacy)
    contra = led.claims[0].contradictions[0]
    assert contra.confidence_score == 0.0  # default
    assert contra.conflict_type == ConflictType.FACTUAL  # default


# ── enrich_ledger (deterministic policy applied to parsed output) ─────────────
def test_enrich_recomputes_status_and_keeps_independent_claim():
    led = parse_ledger(_ledger_json())  # two independent sources, no contradiction
    enriched = enrich_ledger(led)
    assert enriched.claims[0].status == ClaimStatus.KEPT
    # Pure: original is untouched (still the LLM placeholder).
    assert led.claims[0].status == ClaimStatus.UNCORROBORATED


def test_enrich_tags_temporal_drift_on_claim():
    led = ClaimLedger(
        claims=[
            {
                "id": "c1",
                "subtopic_id": "s1",
                "text": "field value",
                "status": "uncorroborated",
                "sources": [
                    {"url": "https://a.com", "etld1": "a.com", "publication_date": "2022-06-01"}
                ],
                "contradictions": [
                    {"url": "https://b.com", "etld1": "b.com", "publication_date": "2020-01-01"}
                ],
            }
        ]
    )
    enriched = enrich_ledger(led)
    claim = enriched.claims[0]
    assert claim.status == ClaimStatus.FLAGGED  # independent contradiction
    assert claim.contradictions[0].conflict_type == ConflictType.TEMPORAL_DRIFT
    # Supporting source (2022) is newer than the contradiction (2020) → CURRENT.
    assert claim.temporal_status == TemporalStatus.CURRENT
    assert 0.0 <= claim.contradictions[0].confidence_score <= 1.0


# ── verify() end-to-end with injected runner (no model) ───────────────────────
async def test_verify_runs_runner_and_applies_policy():
    led = await verify("subtopic payload", runner=_runner_returning(_ledger_json()))
    assert isinstance(led, ClaimLedger)
    # The LLM emitted 'uncorroborated'; policy upgrades to KEPT (2 independent sources).
    assert led.claims[0].status == ClaimStatus.KEPT


async def test_verify_accepts_dict_payload_from_runner():
    led = await verify("payload", runner=_runner_returning(json.loads(_ledger_json())))
    assert led.claims[0].status == ClaimStatus.KEPT


async def test_verify_passes_payload_to_runner():
    captured = {}

    async def _capturing(p: str):
        captured["payload"] = p
        return _ledger_json()

    await verify("  the verifier payload  ", runner=_capturing)
    assert captured["payload"] == "  the verifier payload  "
