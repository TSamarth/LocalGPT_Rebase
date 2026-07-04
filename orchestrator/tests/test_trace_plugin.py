"""
TracePlugin (E6 P2): per-session JSONL trace of agent / model / tool activity.

Two layers of coverage, both fully offline:

1. **Live-ish run** — drive the offline research workflow through an
   ``InMemoryRunner`` with ``TracePlugin`` registered, then assert every line of
   ``trace.jsonl`` parses as JSON and that event records are present.
   (Agent/model/tool records don't appear here because the workflow nodes are
   canned ``@node`` stubs, not LlmAgents, and never reach a real model — those
   record types are covered by the direct-callback tests in layer 2.)

2. **Direct callback unit tests** — invoke ``before/after_model`` and
   ``before/after_tool`` with minimal fake contexts to assert model_request /
   model_response / tool_start / tool_end records (incl. token usage + latency),
   plus the pure-observer contract: a broken context never raises.
"""
from __future__ import annotations

import json
import warnings
from types import SimpleNamespace

from google.adk.apps import App, ResumabilityConfig
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import node
from google.adk.workflow.utils._workflow_hitl_utils import (
    create_request_input_response,
    get_request_input_interrupt_ids,
    has_request_input_function_call,
)
from google.genai import types

from app.runner import build_runner
from app.schemas import (
    Claim,
    ClaimLedger,
    ClaimStatus,
    ClarifyResult,
    CrawlStrategy,
    Depth,
    ResearchPlan,
    ScoredURL,
    SourceRef,
    Subtopic,
)
from app.trace_plugin import TracePlugin
from app.workflow import build_research_workflow

warnings.filterwarnings("ignore", message=r".*ResumabilityConfig.*", category=UserWarning)

RAW_QUERY = "tell me about rust async runtimes"

_APPROVE_PLAN = ResearchPlan(
    subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=2)],
    depth=Depth.SHALLOW,
)


# ── Offline stubs (mirror test_e3s2_t2) ────────────────────────────────────────
def _clarifier_stub():
    @node
    async def _stub(node_input: str) -> ClarifyResult:
        return ClarifyResult(status="clear", normalized_query="normalized")

    return _stub


def _planner_stub():
    @node
    async def _stub(node_input: str) -> ResearchPlan:
        return ResearchPlan(
            subtopics=[Subtopic(id="s1", question="angle 1", target_evidence=2)],
            depth=Depth.SHALLOW,
        )

    return _stub


def _acquirer_stub():
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


def _extractor_stub():
    @node
    async def _stub(node_input: str) -> list[str]:
        return ["page-1"]

    return _stub


def _verifier_stub_one_kept():
    calls = {"i": 0}

    @node
    async def _stub(node_input: str) -> ClaimLedger:
        i = calls["i"]
        calls["i"] += 1
        if i == 0:
            return ClaimLedger(
                claims=[
                    Claim(
                        id="c1",
                        subtopic_id="s1",
                        text="a claim",
                        status=ClaimStatus.UNCORROBORATED,
                        sources=[
                            SourceRef(url="https://a.example/1", etld1="a.example", quote="q1"),
                            SourceRef(url="https://b.example/1", etld1="b.example", quote="q2"),
                        ],
                    )
                ]
            )
        return ClaimLedger(claims=[])

    return _stub


def _writer_stub():
    @node
    async def _stub(node_input: str) -> str:
        return "## Findings\n\nWriter body."

    return _stub


def _build_app():
    workflow = build_research_workflow(
        clarifier_node=_clarifier_stub(),
        planner_node=_planner_stub(),
        acquirer_node=_acquirer_stub(),
        extractor_node=_extractor_stub(),
        verifier_node=_verifier_stub_one_kept(),
        writer_node=_writer_stub(),
    )
    return App(
        name="tracegate",
        root_agent=workflow,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


async def _run_complete(runner, session):
    message = types.Content(role="user", parts=[types.Part(text=RAW_QUERY)])
    cp1_seen = False
    invocation_id = None
    new_message = message
    for _ in range(100):
        pending = None
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if has_request_input_function_call(event):
                iid = get_request_input_interrupt_ids(event)[0]
                invocation_id = event.invocation_id
                if not cp1_seen:
                    cp1_seen = True
                    pending = (iid, _APPROVE_PLAN.model_dump(mode="json"))
                else:
                    pending = (iid, {"result": ""})
        if pending is None:
            break
        reply_part = create_request_input_response(pending[0], pending[1])
        new_message = types.Content(role="user", parts=[reply_part])


# ── Layer 1: live-ish run ──────────────────────────────────────────────────────
async def test_trace_jsonl_written_and_parses(tmp_path):
    """A full offline run produces trace.jsonl; every line parses and agent +
    event records are present."""
    sessions_dir = tmp_path / "sessions"
    plugin = TracePlugin(sessions_dir=str(sessions_dir))

    session_svc = InMemorySessionService()
    runner = build_runner(_build_app(), session_service=session_svc, plugins=[plugin])
    session = await session_svc.create_session(app_name="tracegate", user_id="test-user")

    await _run_complete(runner, session)

    trace_file = sessions_dir / session.id / "trace.jsonl"
    assert trace_file.exists(), "trace.jsonl was not written"

    records = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
    assert records, "trace.jsonl is empty"
    for rec in records:
        assert "ts" in rec and "type" in rec

    # The workflow nodes are plain ``@node`` stubs (not LlmAgents), so only
    # on_event_callback fires here; model/tool/agent callbacks are exercised in
    # the direct-callback tests below.
    types_seen = {r["type"] for r in records}
    assert "event" in types_seen
    # event records carry state_delta_keys; at least one v2_* key was traced.
    delta_keys = {k for r in records if r["type"] == "event" for k in r.get("state_delta_keys", [])}
    assert any(k.startswith("v2_") for k in delta_keys), f"no v2_* state deltas traced: {delta_keys}"


# ── Layer 2: direct callback unit tests ────────────────────────────────────────
def _fake_cb_ctx(session_id: str = "s-model", agent: str = "acquirer"):
    session = SimpleNamespace(id=session_id)
    return SimpleNamespace(invocation_id="inv-1", agent_name=agent, session=session)


async def test_model_callbacks_record_tokens_and_latency(tmp_path):
    plugin = TracePlugin(sessions_dir=str(tmp_path))
    ctx = _fake_cb_ctx()

    llm_request = SimpleNamespace(
        tools_dict={"discover_urls": object(), "score_and_triage_urls": object()},
        contents=[SimpleNamespace(parts=[SimpleNamespace(text="find vector db tradeoffs")])],
    )
    await plugin.before_model_callback(callback_context=ctx, llm_request=llm_request)

    llm_response = SimpleNamespace(
        content=SimpleNamespace(
            parts=[SimpleNamespace(text="", function_call=SimpleNamespace(name="discover_urls", args={"query": "x"}))]
        ),
        usage_metadata=SimpleNamespace(
            prompt_token_count=120, candidates_token_count=8, total_token_count=128
        ),
    )
    await plugin.after_model_callback(callback_context=ctx, llm_response=llm_response)

    records = [
        json.loads(line)
        for line in (tmp_path / "s-model" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    req = next(r for r in records if r["type"] == "model_request")
    assert set(req["tools_offered"]) == {"discover_urls", "score_and_triage_urls"}
    assert "vector db" in req["last_content"]

    resp = next(r for r in records if r["type"] == "model_response")
    assert resp["function_calls"][0]["name"] == "discover_urls"
    assert resp["tokens"] == {"prompt": 120, "response": 8, "total": 128}
    assert isinstance(resp["latency_ms"], int) and resp["latency_ms"] >= 0


async def test_tool_callbacks_record_args_result_latency(tmp_path):
    plugin = TracePlugin(sessions_dir=str(tmp_path))
    session = SimpleNamespace(id="s-tool")
    tool_ctx = SimpleNamespace(invocation_id="inv-2", session=session)
    tool = SimpleNamespace(name="discover_urls")

    await plugin.before_tool_callback(
        tool=tool, tool_args={"query": "vector db"}, tool_context=tool_ctx
    )
    await plugin.after_tool_callback(
        tool=tool, tool_args={"query": "vector db"}, tool_context=tool_ctx, result={"urls": ["a"]}
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "s-tool" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = next(r for r in records if r["type"] == "tool_start")
    end = next(r for r in records if r["type"] == "tool_end")
    assert start["tool"] == "discover_urls" and "vector db" in start["args"]
    assert "urls" in end["result"]
    assert isinstance(end["latency_ms"], int) and end["latency_ms"] >= 0


async def test_error_callbacks_pop_start_entries(tmp_path):
    """Model/tool error callbacks must remove their paired _starts entry so the
    process-wide dict does not leak on a long-lived server (M5)."""
    plugin = TracePlugin(sessions_dir=str(tmp_path))

    # model: before pushes a start; on_model_error must pop it.
    ctx = _fake_cb_ctx()
    llm_request = SimpleNamespace(tools_dict={}, contents=[])
    await plugin.before_model_callback(callback_context=ctx, llm_request=llm_request)
    assert ("inv-1", "model", "acquirer") in plugin._starts
    await plugin.on_model_error_callback(
        callback_context=ctx, llm_request=llm_request, error=RuntimeError("boom")
    )
    assert ("inv-1", "model", "acquirer") not in plugin._starts

    # tool: before pushes a start; on_tool_error must pop it.
    tool_ctx = SimpleNamespace(invocation_id="inv-2", session=SimpleNamespace(id="s-tool"))
    tool = SimpleNamespace(name="discover_urls")
    await plugin.before_tool_callback(tool=tool, tool_args={"q": "x"}, tool_context=tool_ctx)
    assert ("inv-2", "tool", "discover_urls") in plugin._starts
    await plugin.on_tool_error_callback(
        tool=tool, tool_args={"q": "x"}, tool_context=tool_ctx, error=RuntimeError("boom")
    )
    assert ("inv-2", "tool", "discover_urls") not in plugin._starts
    assert plugin._starts == {}


async def test_broken_context_never_raises(tmp_path):
    """Pure-observer contract: a callback given an unusable context logs and
    returns None instead of breaking the run."""
    plugin = TracePlugin(sessions_dir=str(tmp_path))
    bad = SimpleNamespace()  # no invocation_id, no session, no agent_name
    llm_request = SimpleNamespace(tools_dict={}, contents=[])
    # Must not raise; returns None.
    assert await plugin.before_model_callback(callback_context=bad, llm_request=llm_request) is None
