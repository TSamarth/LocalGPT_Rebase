"""
Registrable-domain (eTLD+1) helper.

Computing eTLD+1 correctly requires the public-suffix list — naive
``netloc.split('.')`` gets ``.co.uk``, ``.github.io``, ``.com.au`` etc. wrong.
We use ``tldextract`` with its **bundled** suffix snapshot and disable network
fetches (``suffix_list_urls=()``), so the helper is deterministic and works
fully offline (important for the mocked test suite).
"""
from __future__ import annotations

from functools import lru_cache

import tldextract

# No network: use the snapshot bundled with tldextract. Cache lives in-memory.
_extract = tldextract.TLDExtract(suffix_list_urls=())


@lru_cache(maxsize=4096)
def registrable_domain(url: str) -> str:
    """Return the eTLD+1 (registrable domain) for a URL or host.

    Examples:
        https://blog.example.co.uk/post  -> example.co.uk
        https://arxiv.org/abs/1234       -> arxiv.org
        https://user.github.io/repo      -> user.github.io

    Returns "" when no registrable domain can be derived (e.g. bare IPs,
    localhost, or unparseable input).
    """
    if not url:
        return ""
    try:
        ext = _extract(url)
    except Exception:
        return ""
    # top_domain_under_public_suffix == domain + "." + suffix (the eTLD+1),
    # or "" if absent (the renamed, non-deprecated form of registered_domain).
    return ext.top_domain_under_public_suffix or ""
