# Crawl4AI Integration Specification

How Crawl4AI is integrated as a tool within the Google ADK agent framework.

---

## Role in the Architecture

Crawl4AI is the **content extraction engine**. It sits between search tools (which find URLs) and the storage layer (which persists content):

```
Search Tool → URLs + snippets
                ↓
         Crawl4AI Tool → Clean filtered Markdown
                ↓
         Store Tool → ChromaDB + SQLite
```

It is NOT used for discovery (that's the search tools' job). It is used for **deep content extraction** from known URLs.

---

## Tool Wrapper Design

### `crawl_page` Tool Function

```python
async def crawl_page(urls: str, query_context: str, session_id: str) -> dict:
    """
    Crawl one or more URLs and extract relevant content as clean markdown.
    
    Args:
        urls: Comma-separated list of URLs to crawl.
        query_context: The research sub-query for BM25 content filtering.
            This ensures only content relevant to the research question
            is extracted (not navbars, ads, sidebars, etc.)
        session_id: Research session ID for logging and caching.
    
    Returns:
        dict with:
        - status: "success" or "partial" or "error"
        - results: list of {url, title, content, content_length} for successful crawls
        - failures: list of {url, error} for failed crawls
    """
```

### Implementation Details

```python
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.content_filter_strategy import BM25ContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

async def _crawl_urls(urls: list[str], query_context: str) -> dict:
    browser_config = BrowserConfig(
        headless=True,
        viewport_width=1280,
        viewport_height=720,
        # Modest viewport to reduce rendering load
    )
    
    bm25_filter = BM25ContentFilter(
        user_query=query_context,
        bm25_threshold=1.0  # Only content scoring above threshold
    )
    md_generator = DefaultMarkdownGenerator(content_filter=bm25_filter)
    
    crawler_config = CrawlerRunConfig(
        markdown_generator=md_generator,
        excluded_tags=["nav", "footer", "aside", "header", "script", "style"],
        remove_overlay_elements=True,
        remove_forms=True,
        page_timeout=30000,         # 30 second timeout per page
        exclude_external_links=True, # Don't clutter markdown with external links
    )
    
    results = []
    failures = []
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        crawl_results = await crawler.arun_many(
            urls=urls,
            config=crawler_config,
            max_concurrent=3  # ← HARD LIMIT: 3 concurrent browsers
        )
        
        for result in crawl_results:
            if result.success and result.markdown:
                # Prefer fit_markdown (BM25-filtered) over raw
                content = (
                    result.markdown.fit_markdown 
                    if hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown
                    else str(result.markdown)
                )
                results.append({
                    "url": result.url,
                    "title": result.metadata.get("title", ""),
                    "content": content,
                    "content_length": len(content),
                })
            else:
                failures.append({
                    "url": result.url,
                    "error": str(result.error_message) if hasattr(result, 'error_message') else "Unknown error"
                })
    
    status = "success" if not failures else ("partial" if results else "error")
    return {"status": status, "results": results, "failures": failures}
```

---

## Configuration

### Browser Config
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `headless` | `True` | No GUI needed for automated research |
| `viewport_width` | 1280 | Standard desktop width; avoids mobile layouts |
| `viewport_height` | 720 | Reduce rendering area for performance |

### Crawler Config
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `page_timeout` | 30000 (30s) | Balance between slow sites and not waiting forever |
| `excluded_tags` | nav, footer, aside, header | Remove non-content elements |
| `remove_overlay_elements` | True | Dismiss cookie banners, popups |
| `remove_forms` | True | Forms aren't research content |
| `exclude_external_links` | True | Reduce noise in extracted markdown |
| `max_concurrent` | 3 | Hard limit — 3 × ~300MB = ~900MB RAM |

### BM25 Content Filter
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `user_query` | Sub-query text | Focus extraction on relevant content |
| `bm25_threshold` | 1.0 | Filter out paragraphs with low relevance score |

---

## Resource Management

### Memory Constraints
```
Per browser instance:   ~150-300 MB RAM
Max concurrent:         3 instances
Peak browser RAM:       ~900 MB
Ollama RAM (concurrent):  0 MB (sequential orchestration prevents overlap)
Total crawling peak:    ~900 MB + Python overhead
```

### Concurrency Control
- `max_concurrent=3` is a **hard limit** in the crawler config
- This is NOT configurable per-agent — it's set in the tool implementation
- Can be adjusted via env var `CRAWL_MAX_CONCURRENT` for machines with more/less RAM
- The `arun_many()` call handles queuing internally

### Timeout Strategy
- **Page timeout**: 30 seconds per URL
- **Total crawl budget**: No hard cap, but agents are instructed to crawl max 5 URLs per sub-query × 4-6 sub-queries = 20-30 URLs total
- **At 30s timeout × 30 URLs ÷ 3 concurrent**: Worst case ~5 minutes for crawling phase
- **Typical case**: Most pages load in 3-10s → total crawl time ~30-90 seconds

---

## Error Handling

### Error Categories
| Error | Handling | Agent Sees |
|-------|----------|------------|
| **DNS failure** | Skip URL | `{"url": "...", "error": "DNS resolution failed"}` |
| **Connection timeout** | Skip after 30s | `{"url": "...", "error": "Page timeout"}` |
| **HTTP 403/401** | Skip URL | `{"url": "...", "error": "Access denied"}` |
| **HTTP 404** | Skip URL | `{"url": "...", "error": "Page not found"}` |
| **HTTP 429** | Skip URL | `{"url": "...", "error": "Rate limited"}` |
| **Empty content** | Skip URL | `{"url": "...", "error": "No content extracted"}` |
| **Bot detection** | Skip URL | `{"url": "...", "error": "Bot detected / CAPTCHA"}` |
| **Browser crash** | Log, continue with remaining | Partial results returned |

### Return Status Logic
- `"success"`: All URLs crawled successfully
- `"partial"`: Some URLs succeeded, some failed (most common)
- `"error"`: All URLs failed

The agent receives the full results + failures list and can reason about what worked and what didn't.

---

## Content Quality

### BM25 Filtering Impact
Without BM25 filter, a Wikipedia page about "Mongol invasion of Japan" returns:
- Navigation menus, sidebar links
- "See also" sections
- Edit links, reference formatting
- ~50KB of markdown

With BM25 filter (query: "Mongol invasion Japan 1274 military battle"):
- Only paragraphs relevant to the query
- Main article content
- ~5-15KB of focused markdown

**10x reduction in content volume** while preserving research-relevant information.

### Content Truncation
- If filtered content exceeds 10,000 characters per URL, truncate to first 10,000
- Rationale: Very long content gets chunked anyway; better to have focused chunks from multiple URLs than exhaustive content from one URL
- This limit is configurable via environment variable

---

## Integration with Storage

After crawling, the agent calls `store_content` for each successful result:

```python
# Agent's typical tool usage pattern:
# 1. Search → get URLs
# 2. Crawl URLs
# 3. Store each result

# The crawl tool returns structured results
crawl_result = await crawl_page(urls="url1,url2,url3", query_context="sub-query", session_id="...")

# Agent then calls store for each successful crawl
for result in crawl_result["results"]:
    await store_content(
        session_id="...",
        url=result["url"],
        content=result["content"],
        title=result["title"],
        source_type="web"
    )
```

---

## Anti-Detection Considerations

### Current Approach (v1)
- Use default Crawl4AI headers (Chrome-like user agent)
- `remove_overlay_elements=True` handles most cookie/consent popups
- 3 concurrent connections is respectful

### Future Enhancements (v2+)
- User agent rotation
- Request delays between domains (2-5s)
- Proxy support for rate-limited sources
- Cached DNS resolution
- Domain-specific crawl configs (e.g., different timeout for Wikipedia vs. news sites)

---

## Testing Strategy

### Unit Tests
- Mock `AsyncWebCrawler` responses
- Test URL parsing and validation
- Test error handling for each failure category
- Test BM25 filter configuration

### Integration Tests (require internet)
- Crawl known stable URLs (Wikipedia pages)
- Verify markdown extraction quality
- Verify BM25 filtering reduces content volume
- Verify concurrent crawling respects limits
- Verify timeout behavior with intentionally slow URLs

### Fixtures
```python
@pytest.fixture
def mock_crawl_result():
    """Provides a realistic CrawlResult for testing."""
    return MockCrawlResult(
        success=True,
        url="https://en.wikipedia.org/wiki/Mongol_invasions_of_Japan",
        markdown="## Mongol invasions of Japan\n\nThe Mongol invasions...",
        metadata={"title": "Mongol invasions of Japan - Wikipedia"},
        links={"internal": [...], "external": [...]}
    )
```

