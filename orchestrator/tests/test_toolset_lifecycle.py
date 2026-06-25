"""
MCP toolset lifecycle on the live drive paths (Tier-0, E1.T3).

The Extractor and Verifier drive calls build a crawl4ai ``MCPToolset`` (a stdio
subprocess) when none is injected. Before this fix they leaked one subprocess per
call. The drive call must now ``close()`` any toolset it *owns* (built itself) and
leave an *injected* toolset alone (the caller owns its lifecycle).

Note on scope: the v1 sync bridge runs every handler under its own ``asyncio.run``,
so a stdio toolset is bound to a single drive call and cannot be shared across the N
research passes — a single long-lived toolset is a v2 node-path property (E2.S2.T4).
The achievable+correct Tier-0 invariant tested here is *close-once-per-owned-build,
never-close-when-injected*, which removes the subprocess leak.

E2.S2.T4 adds the v2 node-path lifecycle below: the ``research`` node now builds
each OWNED crawl4ai toolset ONCE per run at loop entry, shares it across every
acquire/extract/verify pass, and closes it once in a ``finally``. The tests there
drive the real workflow (through CP1 into the loop) with the agent factories
monkeypatched to return canned ``@node`` stubs, asserting each owned toolset's
``.closed == 1`` (built-once/closed-once) and that an INJECTED node builds/closes
no toolset (caller-owned), mirroring the *never-close-when-injected* rule above.
"""
from __future__ import annotations

from google.adk.workflow import node

from app import pipeline
from app.agents import acquirer, extractor, verifier
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceRef,
    Subtopic,
)
from app.workflow import build_research_workflow
from tests.test_workflow import (
    _drive_to_cp1_and_resume,
    _single_subtopic_planner_stub,
    _writer_stub,
)


class _FakeToolset:
    """Records ``close()`` calls; stands in for an MCPToolset offline."""

    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


async def _noop_drive(_agent, _app_name, _text, _output_key) -> str:
    return ""


# ── Extractor path ───────────────────────────────────────────────────────────
async def test_drive_extractor_closes_owned_toolset(monkeypatch):
    fake = _FakeToolset()
    monkeypatch.setattr(extractor, "_build_default_toolset", lambda: fake)
    monkeypatch.setattr(extractor, "build_extractor", lambda _ts: object())
    monkeypatch.setattr(pipeline, "_drive", _noop_drive)

    await pipeline._drive_extractor([], None)

    assert fake.closed == 1


async def test_drive_extractor_leaves_injected_toolset_open(monkeypatch):
    fake = _FakeToolset()
    monkeypatch.setattr(extractor, "build_extractor", lambda _ts: object())
    monkeypatch.setattr(pipeline, "_drive", _noop_drive)

    await pipeline._drive_extractor([], fake)

    assert fake.closed == 0


# ── Verifier path ────────────────────────────────────────────────────────────
async def test_verify_runner_closes_owned_toolset(monkeypatch):
    fake = _FakeToolset()
    monkeypatch.setattr(verifier, "_build_default_toolset", lambda: fake)
    monkeypatch.setattr(verifier, "build_verifier", lambda *, toolset: object())
    monkeypatch.setattr(pipeline, "_drive", _noop_drive)

    runner = pipeline._make_default_verify_runner(None)
    await runner("payload")

    assert fake.closed == 1


async def test_verify_runner_leaves_injected_toolset_open(monkeypatch):
    fake = _FakeToolset()
    monkeypatch.setattr(verifier, "build_verifier", lambda *, toolset: object())
    monkeypatch.setattr(pipeline, "_drive", _noop_drive)

    runner = pipeline._make_default_verify_runner(fake)
    await runner("payload")

    assert fake.closed == 0


# ── v2 node-path lifecycle (E2.S2 T4): toolset built once / closed once ────────
def _canned_acquirer_node(_toolset) -> object:
    """Factory stub: ignores the (fake) toolset, returns a canned acquirer node."""

    @node
    async def _stub(node_input: str) -> list[ScoredURL]:
        return [
            ScoredURL(
                url="https://a.example/doc",
                score=0.9,
                strategy=CrawlStrategy.CRAWL,
                etld1="a.example",
            )
        ]

    return _stub


def _canned_extractor_node(_toolset) -> object:
    """Factory stub: returns a canned extractor node (page-ids discarded by loop)."""

    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]

    return _stub


def _canned_verifier_node(*, toolset) -> object:
    """Factory stub (keyword-only ``toolset``, matching ``build_verifier``).

    Returns a node emitting one KEPT-able claim (two independent sources) so the
    single-subtopic loop stops on target-met after a single pass — keeping the
    run short while still exercising every acquire/extract/verify node once.
    """

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        return ClaimLedger(
            claims=[
                Claim(
                    id="c1",
                    subtopic_id="s1",
                    text="assertion c1",
                    status=ClaimStatus.UNCORROBORATED,
                    sources=[
                        SourceRef(url="https://a.example/1", etld1="a.example", quote="q1"),
                        SourceRef(url="https://b.example/1", etld1="b.example", quote="q2"),
                    ],
                )
            ]
        )

    return _stub


def _approve_one_subtopic() -> ResearchPlan:
    """A 1-subtopic SHALLOW plan with target_evidence=1 → loop stops after 1 pass."""
    return ResearchPlan(
        subtopics=[Subtopic(id="s1", question="only angle", target_evidence=1)],
        depth=Depth.SHALLOW,
    )


async def test_node_path_builds_and_closes_each_owned_toolset_once(monkeypatch):
    """T4 gate: with NO injected agent nodes, the ``research`` node builds each
    owned crawl4ai toolset and closes EVERY built instance exactly once in
    ``finally`` — the no-leak invariant (close-once-per-owned-build).

    Each ``_build_default_toolset`` mints a FRESH ``_FakeToolset`` per call (as the
    real factory mints a new ``MCPToolset`` per call), and we collect them. The
    ``research`` node is ``rerun_on_resume=True``, so ADK replays the parent on each
    resume hop: once after the CP1 plan-approval pause, and again after the CP2
    draft-approval pause (E3.S1 T1). Each replay builds a fresh owned toolset at
    loop entry (the cached loop ``run_node`` calls are checkpoint-skipped) and
    closes THAT instance once in ``finally`` — so every built instance has
    ``closed == 1`` and none leaks. The builders return canned ``@node`` stubs and a
    writer stub is injected, so the run is fully offline.
    """
    acquirer_built: list[_FakeToolset] = []
    extractor_built: list[_FakeToolset] = []
    verifier_built: list[_FakeToolset] = []

    def _mint(bucket):
        ts = _FakeToolset()
        bucket.append(ts)
        return ts

    monkeypatch.setattr(acquirer, "_build_default_toolset", lambda: _mint(acquirer_built))
    monkeypatch.setattr(extractor, "_build_default_toolset", lambda: _mint(extractor_built))
    monkeypatch.setattr(verifier, "_build_default_toolset", lambda: _mint(verifier_built))
    monkeypatch.setattr(acquirer, "build_acquirer", _canned_acquirer_node)
    monkeypatch.setattr(extractor, "build_extractor", _canned_extractor_node)
    monkeypatch.setattr(verifier, "build_verifier", _canned_verifier_node)

    # Build with NO acquirer/extractor/verifier nodes injected → all defaulted →
    # all three toolsets are OWNED by the node. Clarifier/planner/writer are canned
    # (no toolsets) so the slice runs offline through the loop and CP2.
    workflow = build_research_workflow(
        clarifier_node=_simple_clarifier(),
        planner_node=_single_subtopic_planner_stub(1, Depth.SHALLOW),
        writer_node=_writer_stub(),
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=_approve_one_subtopic())

    assert isinstance(result, ClaimLedger)
    # At least one owned toolset of each kind was built, and EVERY built instance
    # was closed exactly once (no double-close on the same instance, no leak).
    assert acquirer_built and extractor_built and verifier_built
    for ts in acquirer_built + extractor_built + verifier_built:
        assert ts.closed == 1, f"owned toolset not closed exactly once: closed={ts.closed}"


async def test_node_path_injected_agent_builds_no_toolset(monkeypatch):
    """T4 gate: an INJECTED agent node is caller-owned — the node builds and closes
    NO toolset for it (``_build_default_toolset`` is never called for that agent).

    We inject canned acquirer/extractor/verifier nodes and make every
    ``_build_default_toolset`` raise: if the node tried to build an owned toolset
    for an injected agent the run would blow up. A clean terminal ledger proves no
    toolset was built (and so none was closed) for the injected path.
    """
    def _boom() -> object:
        raise AssertionError("owned toolset built for an INJECTED agent node")

    monkeypatch.setattr(acquirer, "_build_default_toolset", _boom)
    monkeypatch.setattr(extractor, "_build_default_toolset", _boom)
    monkeypatch.setattr(verifier, "_build_default_toolset", _boom)

    workflow = build_research_workflow(
        clarifier_node=_simple_clarifier(),
        planner_node=_single_subtopic_planner_stub(1, Depth.SHALLOW),
        acquirer_node=_canned_acquirer_node(None),
        extractor_node=_canned_extractor_node(None),
        verifier_node=_canned_verifier_node(toolset=None),
    )
    result = await _drive_to_cp1_and_resume(workflow, approved_plan=_approve_one_subtopic())

    assert isinstance(result, ClaimLedger)
    assert result.kept_count("s1") == 1


def _simple_clarifier() -> object:
    """Minimal canned clarifier node (no toolset) for the node-path lifecycle tests."""

    from app.schemas import ClarifyResult

    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(status="clear", normalized_query="normalized: " + str(node_input).strip())

    return _stub
