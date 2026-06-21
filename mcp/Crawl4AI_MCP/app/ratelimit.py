"""
Per-domain rate limiting + transient-failure backoff for crawling.

Two cooperating pieces:

  - DomainRateLimiter: enforces a minimum spacing (config.PER_DOMAIN_RATE_LIMIT_SEC)
    between requests to the same registrable domain (eTLD+1). A single shared
    instance is used by the crawl tools so concurrent crawls of the same host
    are politely serialised, while different hosts proceed in parallel.

  - crawl_with_retry: retries a crawl coroutine on HTTP 429 / transient failures
    with exponential backoff (config.RATE_LIMIT_MAX_RETRIES attempts,
    config.RATE_LIMIT_BACKOFF_BASE_SEC base).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional

from app.config import config
from app.domain import registrable_domain


class DomainRateLimiter:
    """Throttle requests so the same eTLD+1 is hit at most once per interval."""

    def __init__(self, min_interval_sec: Optional[float] = None):
        self._min_interval = (
            min_interval_sec if min_interval_sec is not None else config.PER_DOMAIN_RATE_LIMIT_SEC
        )
        self._next_allowed: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, url: str) -> None:
        """Block until a request to ``url``'s domain is allowed, then reserve the slot."""
        if self._min_interval <= 0:
            return
        domain = registrable_domain(url) or url
        async with self._locks[domain]:
            now = time.monotonic()
            wait = self._next_allowed[domain] - now
            if wait > 0:
                await asyncio.sleep(wait)
            # Reserve the next slot relative to the moment this request proceeds.
            self._next_allowed[domain] = time.monotonic() + self._min_interval


def _is_rate_limited(result: Any) -> bool:
    """True when a CrawlResult looks like a 429 / rate-limit response."""
    status = getattr(result, "status_code", None)
    return status == 429


def _is_transient_failure(result: Any) -> bool:
    """True when a CrawlResult failed with a retryable (5xx / network) error."""
    if getattr(result, "success", True):
        return False
    status = getattr(result, "status_code", None)
    if status is not None and 500 <= status < 600:
        return True
    # No status (network error / timeout) is treated as transient.
    return status is None


async def crawl_with_retry(
    do_crawl: Callable[[], Awaitable[Any]],
    *,
    max_retries: Optional[int] = None,
    backoff_base: Optional[float] = None,
    ctx: Any = None,
) -> Any:
    """Run ``do_crawl`` with exponential-backoff retries on 429 / transient errors.

    ``do_crawl`` must be a zero-arg coroutine factory returning a CrawlResult.
    Returns the last CrawlResult (successful or not) once retries are exhausted.
    """
    retries = max_retries if max_retries is not None else config.RATE_LIMIT_MAX_RETRIES
    base = backoff_base if backoff_base is not None else config.RATE_LIMIT_BACKOFF_BASE_SEC

    attempt = 0
    result = await do_crawl()
    while attempt < retries and (_is_rate_limited(result) or _is_transient_failure(result)):
        delay = base * (2 ** attempt)
        if ctx:
            reason = "429 rate-limited" if _is_rate_limited(result) else "transient failure"
            await ctx.warning(f"  {reason}; backoff {delay:.1f}s (attempt {attempt + 1}/{retries})")
        await asyncio.sleep(delay)
        attempt += 1
        result = await do_crawl()
    return result


# Shared limiter used by the crawl tools.
_limiter: Optional[DomainRateLimiter] = None


def get_rate_limiter() -> DomainRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = DomainRateLimiter()
    return _limiter
