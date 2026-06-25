"""
CP3 verb dispatcher — a PURE port of v1's ``cp3_checkpoint`` (``checkpoint.py``).

CP3 is the deep-only "candidate sources" checkpoint: after the Acquirer triages a
list of ``ScoredURL``s, the human inspects the list and may add / exclude / redirect
URLs before extraction. v1 ran this as a multi-line interactive stdin loop. In the
v2 ADK node path the human reply arrives as a **single ``RequestInput`` response
string** (free text), so this adapter is PURE: it takes the current URL list + that
one reply string and applies the verbs deterministically — NO ``input()``, NO
printing, NO LLM.

Grammar (ported 1:1 from v1 ``cp3_checkpoint``):
  - ``+ <url>``        → add ``<url>`` as a user-forced candidate
  - ``- <n>``          → exclude index ``n`` (only when ``n.isdigit()`` and ``int(n) < len(working)``)
  - ``r <url>``        → redirect: add ``<url>`` as a steered target
  - ``d``              → done (stop processing further lines)

Reply shape: one verb per non-empty line. The reply may carry several
newline-separated verb lines; they are applied in order, and processing stops at
the first ``d``/``done`` line (or end of input) — exactly as v1's stdin loop
returned on ``d``. Unrecognized lines are ignored (v1 printed "Unrecognized
action." and continued; here we just skip, since there is no console).

``needs_supplemental`` is ``True`` when the user **added or redirected** a URL
(NOT on exclude) — it triggers a one-shot Acquirer re-entry so the new targets
get crawled (v1 appended ``ResearchPhase.ACQUIRE``).

Output: ``(filtered_urls, needs_supplemental)``.

Like ``research_policy``, this module imports only ``config``/``schemas`` (here just
``schemas``) so the workflow loop can use it without dragging in the v1 driver.
"""
from __future__ import annotations

from .schemas import CrawlStrategy, ScoredURL


def _user_url(url: str) -> ScoredURL:
    """A user-forced candidate: max relevance, plain crawl, sourced as ``user``.

    Ported verbatim from v1 ``checkpoint._user_url``.
    """
    return ScoredURL(url=url, score=1.0, strategy=CrawlStrategy.CRAWL, source="user")


def apply_cp3_verbs(
    urls: list[ScoredURL], reply: str
) -> tuple[list[ScoredURL], bool]:
    """Apply the CP3 verb grammar in ``reply`` to ``urls`` deterministically.

    Each non-empty line of ``reply`` is one verb (same ``verb, _, arg`` split as
    v1). Processing stops at the first ``d``/``done`` line (or end of input).

    Args:
        urls: the current candidate ``ScoredURL`` list (from the Acquirer).
        reply: the human's free-text ``RequestInput`` response — may contain
            multiple newline-separated verb lines.

    Returns:
        ``(filtered_urls, needs_supplemental)`` where ``needs_supplemental`` is
        ``True`` iff the user added or redirected at least one URL.
    """
    working = list(urls)
    needs_supplemental = False

    for line in reply.splitlines():
        action = line.strip()
        if not action:
            continue
        verb, _, arg = action.partition(" ")
        verb, arg = verb.lower(), arg.strip()

        if verb in ("d", "done"):
            break
        if verb == "+" and arg:
            working.append(_user_url(arg))
            needs_supplemental = True
        elif verb == "-" and arg.isdigit() and int(arg) < len(working):
            working.pop(int(arg))
        elif verb in ("r", "redirect") and arg:
            working.append(_user_url(arg))
            needs_supplemental = True
        # else: unrecognized line — ignored (no console to warn on)

    return working, needs_supplemental
