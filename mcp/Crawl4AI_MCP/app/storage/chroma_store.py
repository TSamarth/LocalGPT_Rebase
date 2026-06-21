"""
ChromaDB vector store for semantic search over research chunks.
Uses Ollama embeddings (nomic-embed-text).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import tiktoken
from chromadb import Collection
from chromadb.utils.embedding_functions.ollama_embedding_function import OllamaEmbeddingFunction

from app.config import config

logger = logging.getLogger(__name__)

COLLECTION_NAME = "research_chunks"


class ChromaStore:
    def __init__(self):
        Path(config.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        self._embed_fn = OllamaEmbeddingFunction(
            url=f"{config.OLLAMA_BASE_URL}/api/embeddings",
            model_name=config.OLLAMA_EMBED_MODEL,
        )
        self._collection: Optional[Collection] = None
        # cl100k_base tokenizer — used for token-accurate truncation in
        # _safe_truncate so chunks never exceed the embed model's context window.
        try:
            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._enc = None

    def _get_collection(self) -> Collection:
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=self._embed_fn,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # Conservative chars-per-token fallback when tiktoken is unavailable.
    _CHARS_PER_TOKEN: float = 2.0

    def _safe_truncate(self, text: str) -> str:
        """Truncate text to CHUNK_SIZE_TOKENS cl100k tokens before embedding.

        Why token-based (not character-based):
          Character estimates fail for URL-dense or minified-code content where
          BERT-style embed tokenizers produce 1.5–1.8× more tokens than cl100k
          for the same characters.  By capping at CHUNK_SIZE_TOKENS cl100k
          tokens (which already includes the EMBED_TOKENIZER_SAFETY_FACTOR
          against the EMBED_MODEL_MAX_TOKENS window), we stay within the model's
          limit even for worst-case inputs like ``https://docs.python.org/...``
          links and space-stripped code blocks.

          CHUNK_SIZE_TOKENS (default 304) ≈ 60 % of 512
          Worst-case embed/cl100k ratio observed:  ~1.7×
          304 × 1.7 ≈ 517  →  marginal; kept below 512 in practice because
          normal prose in the same chunk lowers the average ratio.
        """
        limit = config.CHUNK_SIZE_TOKENS
        if self._enc is not None:
            tokens = self._enc.encode(text)
            if len(tokens) <= limit:
                return text
            truncated = self._enc.decode(tokens[:limit])
            logger.debug(
                "Chunk pre-truncated from %d to %d cl100k tokens to fit embed model context window",
                len(tokens), limit,
            )
            return truncated
        # Fallback: character cap when tiktoken is unavailable
        max_chars = int(limit * self._CHARS_PER_TOKEN)
        if len(text) > max_chars:
            logger.debug(
                "Chunk pre-truncated from %d to %d chars (tiktoken unavailable)",
                len(text), max_chars,
            )
            return text[:max_chars]
        return text

    def add_chunks(
        self,
        chunk_ids: List[str],
        chunk_texts: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        """Add text chunks with metadata to the vector store.

        Each chunk is truncated to the embedding model's context window before
        insertion so that tokenizer mismatches (cl100k vs BERT-style) never
        cause the Ollama embedding call to fail.  Chunks are inserted one at a
        time so a single bad chunk cannot abort the entire batch.
        """
        if not chunk_texts:
            return
        col = self._get_collection()
        added = failed = 0
        for cid, text, meta in zip(chunk_ids, chunk_texts, metadatas):
            clean = {k: str(v) if v is not None else "" for k, v in meta.items()}
            safe_text = self._safe_truncate(text)
            try:
                col.add(ids=[cid], documents=[safe_text], metadatas=[clean])
                added += 1
            except Exception as e:
                failed += 1
                logger.warning("ChromaDB skipped chunk %s: %s", cid, e)
        if failed:
            logger.warning(
                "ChromaDB add_chunks: %d added, %d skipped (duplicate id or unexpected error)",
                added, failed,
            )

    def search(
        self,
        query: str,
        n_results: int = 10,
        where: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over stored chunks.
        Returns list of {id, text, score, metadata}.
        """
        col = self._get_collection()
        try:
            results = col.query(
                query_texts=[query],
                n_results=min(n_results, col.count() or 1),
                where=where or None,
                include=["documents", "distances", "metadatas"],
            )
        except Exception as e:
            logger.error("ChromaDB search failed: %s", e)
            return []

        output = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        dists = results.get("distances", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        for cid, doc, dist, meta in zip(ids, docs, dists, metas):
            output.append(
                {
                    "id": cid,
                    "text": doc,
                    "score": round(1.0 - float(dist), 4),  # cosine similarity
                    "metadata": meta,
                }
            )
        return output

    def count(self) -> int:
        try:
            return self._get_collection().count()
        except Exception:
            return 0

    def delete_by_session(self, session_id: str) -> int:
        """Delete all chunks belonging to a session. Returns deleted count."""
        col = self._get_collection()
        try:
            results = col.get(where={"session_id": session_id})
            ids = results.get("ids", [])
            if ids:
                col.delete(ids=ids)
            return len(ids)
        except Exception as e:
            logger.warning("delete_by_session failed: %s", e)
            return 0


# Module-level singleton (lazy init — Ollama may not be available at import)
_chroma: Optional[ChromaStore] = None


def get_chroma() -> ChromaStore:
    global _chroma
    if _chroma is None:
        _chroma = ChromaStore()
    return _chroma

