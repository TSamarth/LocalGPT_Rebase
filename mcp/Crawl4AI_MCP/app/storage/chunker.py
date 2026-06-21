"""
Chunking utilities for research content storage.

Provides two approaches:
  1. TextChunker – legacy token-aware sliding window (kept for backward compat).
  2. chunk_text() – uses Crawl4AI native chunking strategies (RegexChunking,
     SlidingWindowChunking, OverlappingWindowChunking) configured via config.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

import tiktoken
from crawl4ai.chunking_strategy import (
    OverlappingWindowChunking,
    RegexChunking,
    SlidingWindowChunking,
)

from app.config import config


@dataclass
class TextChunk:
    text: str
    chunk_index: int
    token_count: int   # word count used as proxy when using Crawl4AI strategies
    char_start: int
    char_end: int


# ---------------------------------------------------------------------------
# Crawl4AI native chunking (new primary path)
# ---------------------------------------------------------------------------

def get_crawl4ai_chunker(
    strategy: Optional[str] = None,
) -> Union[RegexChunking, SlidingWindowChunking, OverlappingWindowChunking]:
    """Return a Crawl4AI chunking strategy instance based on config (or override).

    Args:
        strategy: Override for config.CHUNKING_STRATEGY.
                  One of "regex" | "sliding_window" | "overlapping".
    """
    s = (strategy or config.CHUNKING_STRATEGY).lower()

    if s == "regex":
        # Split patterns are comma-separated in config
        patterns = [p.strip() for p in config.REGEX_CHUNKING_PATTERNS.split(",") if p.strip()]
        return RegexChunking(patterns=patterns or [r"\n\n"])

    if s == "overlapping":
        return OverlappingWindowChunking(
            window_size=config.CHUNK_WINDOW_SIZE_WORDS,
            overlap=config.CHUNK_OVERLAP_WORDS,
        )

    # Default: sliding_window
    return SlidingWindowChunking(
        window_size=config.CHUNK_WINDOW_SIZE_WORDS,
        step=config.CHUNK_STEP_SIZE_WORDS,
    )


def chunk_text(text: str, strategy: Optional[str] = None) -> List[TextChunk]:
    """Chunk text using a Crawl4AI native chunking strategy.

    Applies the configured (or explicitly specified) strategy to *text* and
    returns a list of :class:`TextChunk` objects compatible with the existing
    storage pipeline.  Word count is used as the ``token_count`` proxy since
    the Crawl4AI strategies are word-based, not token-based.

    Args:
        text: Markdown/plain text to chunk (typically fit_markdown).
        strategy: Optional override for config.CHUNKING_STRATEGY.
    """
    if not text or not text.strip():
        return []

    chunker = get_crawl4ai_chunker(strategy)
    raw_chunks: List[str] = chunker.chunk(text)

    chunks: List[TextChunk] = []
    search_start = 0

    for i, chunk_str in enumerate(raw_chunks):
        if not chunk_str or not chunk_str.strip():
            continue

        # Locate chunk in original text for approximate char positions
        pos = text.find(chunk_str, search_start)
        if pos == -1:
            pos = search_start  # fallback: mark at current position

        char_start = pos
        char_end = pos + len(chunk_str)
        search_start = max(search_start, pos + 1)

        chunks.append(
            TextChunk(
                text=chunk_str,
                chunk_index=i,
                token_count=len(chunk_str.split()),  # word count proxy
                char_start=char_start,
                char_end=char_end,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Legacy TextChunker (kept for backward compatibility and existing tests)
# ---------------------------------------------------------------------------

class TextChunker:
    """
    Legacy token-aware sliding window text chunker (tiktoken-based).

    .. deprecated::
        Prefer :func:`chunk_text` which uses Crawl4AI native strategies.
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap
        try:
            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._enc = None  # fallback to word-based estimation

    def _tokenize(self, text: str) -> List[int]:
        if self._enc:
            return self._enc.encode(text)
        words = text.split()
        return list(range(len(words)))

    def _decode_tokens(self, tokens: List[int], original_text: str) -> str:
        if self._enc:
            try:
                return self._enc.decode(tokens)
            except Exception:
                pass
        words = original_text.split()
        if not tokens:
            return ""
        start = tokens[0]
        end = tokens[-1] + 1
        return " ".join(words[start:end])

    def chunk(self, text: str, url: str = "", title: str = "") -> List[TextChunk]:
        """Chunk text into overlapping token windows."""
        if not text or not text.strip():
            return []

        tokens = self._tokenize(text)
        if not tokens:
            return []

        chunks: List[TextChunk] = []
        step = max(1, self.chunk_size - self.overlap)
        chunk_index = 0

        for start in range(0, len(tokens), step):
            end = min(start + self.chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]

            if not chunk_tokens:
                break

            chunk_text_str = self._decode_tokens(chunk_tokens, text)
            if not chunk_text_str.strip():
                continue

            char_start = len(self._decode_tokens(tokens[:start], text))
            char_end = char_start + len(chunk_text_str)

            chunks.append(
                TextChunk(
                    text=chunk_text_str,
                    chunk_index=chunk_index,
                    token_count=len(chunk_tokens),
                    char_start=char_start,
                    char_end=char_end,
                )
            )
            chunk_index += 1

            if end >= len(tokens):
                break

        return chunks

