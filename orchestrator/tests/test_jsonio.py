"""Lenient JSON extraction (tolerates code fences + trailing prose from live models)."""
from __future__ import annotations

import pytest

from app.jsonio import (
    JSON_ONLY_REASK,
    has_json,
    invoke_json_with_retry,
    loads_first_json,
)


def test_clean_object_and_array():
    assert loads_first_json('{"a": 1}') == {"a": 1}
    assert loads_first_json("[1, 2, 3]") == [1, 2, 3]


def test_strips_trailing_prose():
    assert loads_first_json('[{"url": "x"}]\nHere are the results.') == [{"url": "x"}]


def test_unwraps_code_fence():
    raw = '```json\n{"depth": "deep"}\n```'
    assert loads_first_json(raw) == {"depth": "deep"}


def test_array_with_preamble_and_trailer():
    raw = 'Sure! Here:\n[1, 2]\nDone.'
    assert loads_first_json(raw) == [1, 2]


def test_raises_when_no_json():
    with pytest.raises(ValueError):
        loads_first_json("no json here at all")


# ── has_json / invoke_json_with_retry (E0.S2) ─────────────────────────────────
def test_has_json_predicate():
    assert has_json('[{"url": "x"}]\ntrailing') is True
    assert has_json("pure prose, a tool timed out") is False


async def test_invoke_retry_reasks_once_on_prose_then_returns_json():
    prompts: list[str] = []

    async def runner(prompt: str):
        prompts.append(prompt)
        return "the tool timed out" if len(prompts) == 1 else '{"ok": true}'

    raw = await invoke_json_with_retry(runner, "do work")
    assert raw == '{"ok": true}'
    assert prompts == ["do work", "do work" + JSON_ONLY_REASK]


async def test_invoke_retry_no_reask_when_first_is_json():
    calls = 0

    async def runner(_prompt: str):
        nonlocal calls
        calls += 1
        return '[1, 2]'

    assert await invoke_json_with_retry(runner, "p") == "[1, 2]"
    assert calls == 1


async def test_invoke_retry_passes_through_non_string_output():
    async def runner(_prompt: str):
        return [{"url": "x"}]

    # Already-parsed runner output is returned untouched, no re-ask.
    assert await invoke_json_with_retry(runner, "p") == [{"url": "x"}]
