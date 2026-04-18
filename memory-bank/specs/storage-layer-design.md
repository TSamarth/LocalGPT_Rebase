# Storage Layer Design

Hybrid storage: ChromaDB (vectors) + SQLite (metadata).

---

## Architecture Overview

```
┌──────────────────────────────────────────┐
│           Storage Manager                │
│        (storage/__init__.py)             │
│  Unified store + retrieve interface      │
│  Coordinates ChromaDB ↔ SQLite           │
│  Manages embedding generation            │
└──────┬────────────────────────┬──────────┘
       │                        │
┌──────▼──────────┐   ┌────────▼─────────┐
│  Vector Store    │   │  Metadata Store  │
│  (ChromaDB)      │   │  (SQLite)        │
│  Embedded mode   │   │  research.db     │
│                  │   │                  │
│  Collections:    │   │  Tables:         │
│  - session_{id}  │   │  - sessions      │
│  Per-chunk:      │   │  - sources       │
│  - document text │   │  - chunks        │
│  - embedding vec │   │                  │
│  - metadata dict │   │                  │
└──────────────────┘   └──────────────────┘
       │
┌──────▼──────────┐
│  Chunker         │
│  512 tokens      │
│  50 overlap      │
│  Paragraph-aware │
└──────────────────┘
```

---

## SQLite Schema

### Table: `sessions`
```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    user_query      TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'in_progress',   -- in_progress | completed | failed
    report_path     TEXT,
    planning_output TEXT,                          -- JSON
    metadata        TEXT                           -- JSON
);
```

### Table: `sources`
```sql
CREATE TABLE IF NOT EXISTS sources (
    source_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(session_id),
    url               TEXT NOT NULL,
    domain            TEXT NOT NULL,
    title             TEXT,
    source_type       TEXT NOT NULL DEFAULT 'web', -- web | academic | scholar_abstract
    crawl_status      TEXT NOT NULL DEFAULT 'success', -- success|failed|timeout|blocked
    content_hash      TEXT,                        -- SHA-256 for dedup
    content_length    INTEGER,
    chunk_count       INTEGER,
    reliability_score REAL DEFAULT 0.5,            -- 0.0–1.0
    crawled_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    error_message     TEXT,
    UNIQUE(session_id, url)
);

CREATE INDEX idx_sources_session ON sources(session_id);
CREATE INDEX idx_sources_domain ON sources(domain);
CREATE INDEX idx_sources_type ON sources(source_type);
```

### Table: `chunks`
```sql
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,     -- Same ID used in ChromaDB
    source_id   INTEGER NOT NULL REFERENCES sources(source_id),
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    chunk_index INTEGER NOT NULL,
    char_start  INTEGER,
    char_end    INTEGER,
    token_count INTEGER,
    UNIQUE(source_id, chunk_index)
);

CREATE INDEX idx_chunks_session ON chunks(session_id);
CREATE INDEX idx_chunks_source ON chunks(source_id);
```

### Reliability Score Heuristics
```python
RELIABILITY_SCORES = {
    "academic": 0.85,
    "scholar_abstract": 0.80,
    ".edu": 0.75,
    ".gov": 0.75,
    "wikipedia.org": 0.70,
    "britannica.com": 0.75,
    "reuters.com": 0.70,
    "bbc.com": 0.70,
    "apnews.com": 0.70,
    "nytimes.com": 0.65,
    "default_web": 0.50,
    "default_blog": 0.35,
    "default_forum": 0.25,
}
```

---

## ChromaDB Design

### Collection Strategy
- **One collection per session**: `session_{session_id}`
- Persistent at `./data/chroma_db/`
- Distance: **cosine** (`hnsw:space: cosine`)

### Document Schema (per chunk)
```python
collection.add(
    ids=["chunk_id"],
    documents=["chunk text"],
    embeddings=[[0.1, 0.2, ...]],   # 384-dim MiniLM
    metadatas=[{
        "source_url": "https://...",
        "domain": "en.wikipedia.org",
        "source_type": "web",
        "chunk_index": 0,
        "session_id": "research_...",
        "reliability_score": 0.7
    }]
)
```

### Query Patterns
```python
# Basic semantic search
results = collection.query(
    query_embeddings=[embedding], n_results=5,
    include=["documents", "metadatas", "distances"]
)

# Filtered: academic sources only
results = collection.query(
    query_embeddings=[embedding], n_results=5,
    where={"source_type": "academic"}
)

# Multi-filter
results = collection.query(
    query_embeddings=[embedding], n_results=5,
    where={"$and": [
        {"reliability_score": {"$gte": 0.6}},
        {"source_type": {"$in": ["web", "academic"]}}
    ]}
)
```

---

## Chunker Specification

### Algorithm: Paragraph-Aware Recursive Splitting

```python
class TextChunker:
    def __init__(self, chunk_size=512, chunk_overlap=50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[dict]:
        """Returns list of {"text", "chunk_index", "char_start", "char_end", "token_count"}"""
```

### Splitting Strategy (in priority order)
1. Split by double newline (paragraph boundaries)
2. If paragraph > chunk_size: split by single newline
3. If line > chunk_size: split by sentence (`. `)
4. If sentence > chunk_size: split by whitespace
5. Merge small consecutive chunks up to chunk_size
6. Add chunk_overlap tokens from end of chunk N to start of chunk N+1

### Token Counting
- Approximation: word_count ÷ 0.75 ≈ token_count (fast)
- Upgrade path: `tiktoken` cl100k_base (accurate)

### Edge Cases
- Empty text → empty list + warning log
- Text < chunk_size → single chunk, no overlap
- Markdown headers → prefer as chunk boundaries
- Code blocks → keep intact
- URLs → don't split across boundaries

---

## Embedding Generation

### Model: all-MiniLM-L6-v2
```python
from sentence_transformers import SentenceTransformer

class EmbeddingGenerator:
    def __init__(self, model_name="all-MiniLM-L6-v2", device="cpu"):
        self.model = SentenceTransformer(model_name, device=device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.model.encode(query, normalize_embeddings=True).tolist()
```

| Metric | Value |
|--------|-------|
| Dimensions | 384 |
| Speed (CPU) | ~100 chunks/sec |
| RAM | ~100 MB |
| Max seq length | 256 tokens |

Batch size: 32 chunks for efficiency.

---

## Storage Manager (Unified Interface)

```python
class StorageManager:
    def __init__(self, chroma_path, sqlite_path, embedding_model):
        self.vector_store = VectorStore(chroma_path)
        self.metadata_store = MetadataStore(sqlite_path)
        self.embedder = EmbeddingGenerator(embedding_model)
        self.chunker = TextChunker()

    def create_session(self, session_id, user_query) -> None: ...
    def store_content(self, session_id, url, content, title, source_type="web") -> dict: ...
    def retrieve(self, session_id, query, n_results=5, source_type=None, min_reliability=None) -> list[dict]: ...
    def get_session_sources(self, session_id) -> list[dict]: ...
    def check_duplicate(self, session_id, content_hash) -> bool: ...
    def complete_session(self, session_id, report_path) -> None: ...
```

### `store_content` Flow
1. Check duplicate via content_hash → skip if exists
2. Chunk content via TextChunker
3. Generate embeddings via EmbeddingGenerator (batch)
4. Insert into ChromaDB collection
5. Insert source + chunk rows into SQLite
6. Return `{"status": "success", "chunks_stored": N}`

### `retrieve` Flow
1. Embed query via EmbeddingGenerator
2. Query ChromaDB with optional filters
3. Join with SQLite source metadata
4. Return enriched results sorted by similarity

---

## Data Lifecycle

### During Research
1. Runner → `create_session()` → SQLite row
2. Research agents crawl → `store_content()` per URL
3. Fact Verifier → `retrieve()` for cross-referencing
4. Report Writer saves → Runner → `complete_session()`

### Deduplication
- **Within session**: SHA-256 content hash, UNIQUE(session_id, url)
- **Cross session**: Not deduplicated (independent collections)

### Cleanup
- Retained indefinitely by default
- Optional CLI: delete sessions older than N days
- Export: SQLite dump + ChromaDB collection

---

## Storage Sizing

### Per Session (typical)
| Metric | Estimate |
|--------|----------|
| URLs crawled | 15-25 |
| Raw content | 200-500 KB |
| Chunks | 100-300 |
| ChromaDB vectors | 150-450 KB |
| SQLite metadata | 10-30 KB |
| **Total** | **~0.5-1 MB** |

### At Scale (100 sessions)
| Metric | Estimate |
|--------|----------|
| ChromaDB | ~50-100 MB |
| SQLite | ~3-5 MB |
| **Total** | **~55-105 MB** |
