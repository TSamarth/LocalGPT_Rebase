# Limitations & Known Constraints

Honest documentation of what this system can and cannot do.

---

## LLM Limitations (7B Quantized Model)

### Reasoning Depth
- **7B models** are significantly less capable than 70B+ or cloud models at complex multi-step reasoning
- **Impact**: Fact verification may miss subtle contradictions; report synthesis may be shallow for complex topics
- **Mitigation**: Structured prompts with explicit step-by-step instructions; use output_key for structured JSON; chain-of-thought prompting

### Context Window
- **Mistral 7B**: 8,192 tokens (practical limit ~6K with system prompt)
- **Qwen2.5 7B**: 32,768 tokens native (better choice for research)
- **Impact**: Cannot process large volumes of research data in a single call
- **Mitigation**: 
  - Chunked retrieval (top-k only, not full documents)
  - Summaries in session state (not raw content)
  - Section-by-section report generation for long reports

### Structured Output Reliability
- 7B models sometimes produce invalid JSON, especially with complex nested structures
- **Impact**: Query Planner and Fact Verifier outputs may need parsing retry
- **Mitigation**: JSON repair library as fallback; retry with corrective prompt; validate schema before downstream use

### Hallucination Risk
- All LLMs can generate plausible-sounding but incorrect information
- **Impact**: Particularly dangerous in a research tool
- **Mitigation**: 
  - Fact Verifier explicitly cross-references claims against stored sources
  - Report Writer is instructed to only use verified facts
  - Source citations required for all claims
  - "UNVERIFIED" label for single-source claims

---

## Hardware Limitations

### VRAM Budget (8 GB)
- Only one model fits in VRAM at a time
- Cannot run larger models (13B, 30B, 70B) at acceptable quality
- KV cache limits effective context window under memory pressure
- **No concurrent LLM inference**: Sequential agent execution is mandatory, not optional

### RAM Budget (16 GB)
- Cannot increase Crawl4AI concurrency beyond 3-4 browser instances
- Cannot run Ollama + Crawl4AI at full capacity simultaneously
- ChromaDB in-memory index size limited (fine for our scale)
- **Risk**: Memory pressure if OS + background processes consume more than expected 3 GB

### CPU Budget (6c/12t)
- Embedding generation is CPU-bound (~100 chunks/sec is fine)
- Cannot parallelize CPU-heavy tasks effectively
- Crawl4AI rendering is partially CPU-bound

---

## Search & Crawling Limitations

### DuckDuckGo
- No official API — uses an unofficial Python wrapper
- Rate limits are undocumented; aggressive querying may get blocked
- Search result quality varies (no guaranteed freshness or authority ranking)
- Cannot search within specific date ranges reliably
- **Mitigation**: Rate limiting (1-2 second delays between searches), result caching

### Crawl4AI
- Some websites block headless browsers (CAPTCHA, Cloudflare, etc.)
- JavaScript-rendered content may not fully load within 30s timeout
- Paywalled content is inaccessible
- Very large pages (>5MB) may consume excessive memory
- **Mitigation**: Skip failed URLs, continue with what's available, log all failures

### Semantic Scholar API
- Free tier: 100 requests per 5 minutes
- Not all papers have accessible full text
- Coverage varies by field (CS/Bio > Humanities/History)
- **Mitigation**: Store abstracts even when full text is unavailable; rate limit requests

### SerpAPI (Optional)
- Requires paid API key
- Free tier limited to ~100 searches/month
- Google may change its result format, breaking the scraper
- **Mitigation**: Fully optional — system works without it

---

## Storage Limitations

### ChromaDB
- **Approximate nearest neighbor**: Results are approximate, not exact (HNSW algorithm)
- **Metadata filtering limitations**: No full-text search within stored documents (use semantic search instead)
- **Collection size**: Performance degrades beyond ~1M documents per collection (not a concern at our scale)
- **Embedding model max length**: all-MiniLM-L6-v2 truncates at 256 tokens — chunks must be within this limit
- **Mitigation**: 512-token chunks are fine because the embedding captures the gist even if truncated

### SQLite
- **No concurrent writes**: SQLite has a global write lock
- **Impact**: Not a concern — we write sequentially during the store phase
- **File locking**: May have issues on network drives (use local storage only)

### Chunking
- **Fixed size**: 512 tokens may split important context across chunks
- **Overlap helps but doesn't eliminate**: Some logical units may be split poorly
- **Markdown-specific issues**: Tables, lists, and code blocks may not chunk cleanly
- **Mitigation**: Paragraph-boundary-aware splitting; treat markdown headers as hard boundaries

---

## Agent Orchestration Limitations

### No Dynamic Replanning
- The Query Planner runs once at the start
- If research reveals the plan was inadequate (e.g., topic is different than expected), the system doesn't revise the plan
- **Mitigation**: Planner generates broad sub-queries; future v2 could add a replanning step after research

### No Iterative Deepening
- Each sub-query is searched and crawled once
- If initial results are poor, the system doesn't automatically try alternative queries
- **Mitigation**: Future v2 could add a "research quality check" after the research phase that triggers additional searches

### Sequential Bottleneck
- Fact Verification and Report Writing must wait for ALL research to complete
- Even if web search finishes fast, it waits for academic search
- **Mitigation**: ParallelAgent handles the research phase; the sequential steps are intentional for correctness

### Token Budget per Agent Call
| Agent | Estimated Input Tokens | Estimated Output Tokens | Total |
|-------|----------------------|------------------------|-------|
| Query Planner | ~200 (prompt + query) | ~300 (JSON plan) | ~500 |
| Web Search | ~500 (prompt + plan) | ~200 per tool call × 15 calls | ~3,500 |
| Academic Search | ~500 (prompt + plan) | ~200 per tool call × 10 calls | ~2,500 |
| Fact Verifier | ~1,500 (prompt + summaries) | ~1,000 (verified facts JSON) | ~2,500 |
| Report Writer | ~2,000 (prompt + verified facts) | ~2,000 (report text) | ~4,000 |

**Note**: Fact Verifier and Report Writer approach the Mistral 7B context limit. Qwen2.5 7B (32K context) provides much more headroom.

---

## Report Quality Limitations

### Depth vs. Breadth Trade-off
- A 7B model producing a ~2000-word report cannot match the depth of a GPT-4-class model producing a 10,000-word report
- **Mitigation**: Focus on factual accuracy over literary quality; rely on verified data rather than model knowledge

### Domain Coverage
- Performance varies by topic:
  - **Strong**: Well-documented historical events, scientific topics, technology
  - **Moderate**: Current events (depends on what's indexed), cultural topics
  - **Weak**: Niche topics with limited web presence, non-English topics
- **Mitigation**: Acknowledge coverage gaps in the report

### Citation Quality
- Citations link to source URLs, not specific page sections
- Cannot verify if a URL will still be accessible later (link rot)
- No DOI or formal academic citation formatting (v1)
- **Mitigation**: Future v2 could add DOI resolution, citation formatting, and link validation

---

## Scalability Limitations

- Designed for **single-user, single-query-at-a-time** operation
- No request queuing, no multi-user support
- No horizontal scaling (single machine)
- **Not a limitation**: This is a local tool by design

---

## Security Considerations

- **API keys in .env**: Not encrypted, stored in plaintext (standard for local tools)
- **Web crawling**: The system visits arbitrary URLs — could encounter malicious content
- **Mitigation**: Crawl4AI runs in a sandboxed browser; no code execution from crawled content
- **LLM prompt injection**: Crawled content is fed to the LLM — could contain adversarial text
- **Mitigation**: Content is chunked and filtered; agent prompts are strong; verified facts act as a filter

