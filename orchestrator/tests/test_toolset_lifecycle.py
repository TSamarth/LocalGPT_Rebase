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
"""
from __future__ import annotations

from app import pipeline
from app.agents import extractor, verifier


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
