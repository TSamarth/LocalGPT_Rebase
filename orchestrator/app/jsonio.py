"""
Lenient JSON extraction for live model output.

Local models routinely wrap their JSON in ```` ``` ```` fences or trail it with a
sentence of prose, which makes a bare ``json.loads`` raise ``Extra data`` /
``Expecting value``. :func:`loads_first_json` parses the first complete JSON value
(array OR object) and ignores anything around it, so the agent parsers stay robust
against real Ollama output without coupling to any one model's quirks.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, Union

#: Corrective suffix appended when re-asking a tool-agent for JSON-only output
#: after a non-JSON (e.g. timeout-prose) first response (E0.S2).
JSON_ONLY_REASK = (
    "\n\nIMPORTANT: your previous response was not valid JSON. Reply with ONLY the "
    "JSON value the schema requires — no prose, no markdown, no explanation."
)


def loads_first_json(raw: Union[str, bytes]) -> object:
    """Return the first complete JSON value in ``raw``, ignoring fences/trailing text.

    Strict parse first (well-formed output is never mangled); on failure, scan for
    the first ``[`` or ``{`` and ``raw_decode`` from there, which stops at the end
    of that value and discards trailing data."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(text[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("model output contains no parseable JSON value")


def has_json(raw: Union[str, bytes]) -> bool:
    """Return ``True`` iff :func:`loads_first_json` can extract a JSON value from ``raw``."""
    try:
        loads_first_json(raw)
        return True
    except ValueError:
        return False


async def invoke_json_with_retry(
    runner: Callable[[str], Awaitable[object]],
    prompt: str,
) -> object:
    """Invoke a tool-agent ``runner`` and return output that carries parseable JSON.

    Tool-agents (Acquirer/Verifier) occasionally return pure prose — no JSON — when
    their per-URL crawl tools time out on the local box, which would crash the
    downstream parse (E0.S2). This makes the parse path resilient with a single
    bounded retry: if the first response is a non-JSON *string*, re-ask the runner
    once with a JSON-only corrective suffix (:data:`JSON_ONLY_REASK`) and return that
    second response. Non-string runner output (already-parsed ``list``/``dict``) is
    returned untouched. The caller still parses (and may degrade gracefully) so this
    helper stays a thin, model-agnostic retry boundary.
    """
    raw = await runner(prompt)
    if isinstance(raw, str) and not has_json(raw):
        raw = await runner(prompt + JSON_ONLY_REASK)
    return raw
