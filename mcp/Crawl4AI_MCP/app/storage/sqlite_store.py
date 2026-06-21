"""
SQLite storage for full crawl content, metadata, and chunk tracking.
Uses aiosqlite for async operations.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import config

CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS crawl_sessions (
    id          TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    strategy    TEXT NOT NULL DEFAULT 'unknown',
    created_at  TEXT NOT NULL,
    metadata    TEXT
);

CREATE TABLE IF NOT EXISTS crawled_pages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    url             TEXT NOT NULL,
    title           TEXT,
    raw_markdown    TEXT,
    fit_markdown    TEXT,
    word_count      INTEGER DEFAULT 0,
    fit_word_count  INTEGER DEFAULT 0,
    strategy        TEXT,
    total_score     REAL DEFAULT 0.0,
    status_code     INTEGER,
    success         INTEGER DEFAULT 1,
    error           TEXT,
    crawled_at      TEXT NOT NULL,
    metadata        TEXT,
    FOREIGN KEY (session_id) REFERENCES crawl_sessions(id)
);

CREATE TABLE IF NOT EXISTS page_links (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id          TEXT NOT NULL,
    href             TEXT NOT NULL,
    text             TEXT,
    link_type        TEXT DEFAULT 'internal',
    intrinsic_score  REAL DEFAULT 0.0,
    contextual_score REAL DEFAULT 0.0,
    total_score      REAL DEFAULT 0.0,
    FOREIGN KEY (page_id) REFERENCES crawled_pages(id)
);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    page_id       TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    chunk_text    TEXT NOT NULL,
    token_count   INTEGER DEFAULT 0,
    chroma_doc_id TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (page_id) REFERENCES crawled_pages(id)
);

CREATE INDEX IF NOT EXISTS idx_pages_session  ON crawled_pages(session_id);
CREATE INDEX IF NOT EXISTS idx_pages_url      ON crawled_pages(url);
CREATE INDEX IF NOT EXISTS idx_chunks_page    ON chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_links_page     ON page_links(page_id);
"""


class SQLiteStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.SQLITE_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(CREATE_TABLES_SQL)
            await db.commit()

    # ── Sessions ────────────────────────────────────────────────────────────

    async def create_session(
        self, session_id: str, query: str, strategy: str = "unknown", metadata: Optional[Dict] = None
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO crawl_sessions (id, query, strategy, created_at, metadata) VALUES (?,?,?,?,?)",
                (session_id, query, strategy, datetime.utcnow().isoformat(), json.dumps(metadata or {})),
            )
            await db.commit()

    async def get_session(self, session_id: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM crawl_sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # ── Pages ────────────────────────────────────────────────────────────────

    async def save_page(
        self,
        page_id: str,
        session_id: Optional[str],
        url: str,
        title: str,
        raw_markdown: str,
        fit_markdown: str,
        strategy: str = "crawl_url",
        total_score: float = 0.0,
        status_code: Optional[int] = None,
        success: bool = True,
        error: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        word_count = len(raw_markdown.split()) if raw_markdown else 0
        fit_word_count = len(fit_markdown.split()) if fit_markdown else 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO crawled_pages
                   (id, session_id, url, title, raw_markdown, fit_markdown,
                    word_count, fit_word_count, strategy, total_score,
                    status_code, success, error, crawled_at, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    page_id, session_id, url, title, raw_markdown, fit_markdown,
                    word_count, fit_word_count, strategy, total_score,
                    status_code, int(success), error,
                    datetime.utcnow().isoformat(), json.dumps(metadata or {}),
                ),
            )
            await db.commit()

    async def get_page_by_url(self, url: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM crawled_pages WHERE url = ? ORDER BY crawled_at DESC LIMIT 1", (url,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def save_links(self, page_id: str, links: List[Dict]) -> None:
        if not links:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """INSERT INTO page_links
                   (page_id, href, text, link_type, intrinsic_score, contextual_score, total_score)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        page_id,
                        lnk.get("href", ""),
                        lnk.get("text", ""),
                        lnk.get("link_type", "internal"),
                        lnk.get("intrinsic_score", 0.0),
                        lnk.get("contextual_score", 0.0),
                        lnk.get("total_score", 0.0),
                    )
                    for lnk in links
                ],
            )
            await db.commit()

    # ── Chunks ───────────────────────────────────────────────────────────────

    async def save_chunks(self, page_id: str, chunks: List[Dict]) -> None:
        """chunks: list of {id, chunk_index, chunk_text, token_count, chroma_doc_id}"""
        if not chunks:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """INSERT OR IGNORE INTO chunks
                   (id, page_id, chunk_index, chunk_text, token_count, chroma_doc_id, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        c["id"], page_id, c["chunk_index"], c["chunk_text"],
                        c.get("token_count", 0), c.get("chroma_doc_id"),
                        datetime.utcnow().isoformat(),
                    )
                    for c in chunks
                ],
            )
            await db.commit()

    # ── Stats ────────────────────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM crawl_sessions") as c:
                sessions = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM crawled_pages WHERE success = 1") as c:
                pages = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM chunks") as c:
                chunks = (await c.fetchone())[0]
            async with db.execute(
                "SELECT url, title, total_score FROM crawled_pages WHERE success=1 ORDER BY total_score DESC LIMIT 5"
            ) as c:
                top_pages = [{"url": r[0], "title": r[1], "score": r[2]} for r in await c.fetchall()]

        return {
            "sessions": sessions,
            "pages_crawled": pages,
            "chunks_stored": chunks,
            "top_pages_by_score": top_pages,
        }


# Module-level singleton
_store: Optional[SQLiteStore] = None


async def get_store() -> SQLiteStore:
    global _store
    if _store is None:
        _store = SQLiteStore()
        await _store.init()
    return _store

