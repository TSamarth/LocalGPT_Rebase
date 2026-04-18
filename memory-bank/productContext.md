# Product Context: LocalGPT Deep Research Agent

## Why This Project Exists

### The Problem
Current deep research tools fall into two categories:
1. **Cloud-based AI research tools** (Perplexity, ChatGPT Deep Research, Gemini Deep Research): Powerful but require sending all queries and data to third-party servers. Expensive at scale. Subject to rate limits, content policies, and privacy concerns.
2. **Manual research workflows**: Users manually search, read, cross-reference, and synthesize information. Time-consuming, inconsistent, and prone to confirmation bias.

There is no good **local, privacy-first** solution that combines autonomous multi-source research with fact verification and structured report generation — all running on consumer hardware.

### The Opportunity
With the maturation of:
- **Quantized open-source LLMs** (7B models that fit in 8GB VRAM)
- **Google ADK** (structured multi-agent orchestration framework)
- **Crawl4AI** (async web crawling with clean markdown extraction)
- **Local vector databases** (ChromaDB, embedded mode)

…it is now feasible to build a production-quality research agent that runs entirely on a single desktop machine.

## What This Product Does

### Core Workflow
```
User Query → Query Planning → Multi-Source Search → Web Crawling → 
Content Storage → Fact Verification → Report Synthesis → Markdown Report
```

1. **User submits a research query** via CLI (e.g., `"Mongol Invasion of Japan in 1274"`)
2. **Query Planner** decomposes it into 3–6 targeted sub-questions covering different angles (causes, events, key figures, aftermath, historiography)
3. **Research Agents** work in parallel:
   - Web Search Agent queries DuckDuckGo, gets URLs + snippets
   - Academic Search Agent queries Semantic Scholar / SerpAPI for scholarly sources
4. **Crawl4AI** crawls the top URLs from both agents, extracting full-page content as clean, relevance-filtered markdown
5. **Storage Layer** chunks the content, generates embeddings, and stores in ChromaDB (vectors) + SQLite (metadata)
6. **Fact Verification Agent** retrieves passages semantically, cross-references claims across sources, flags contradictions, and produces a verified facts list
7. **Report Writer Agent** synthesizes verified facts into a structured Markdown report with proper citations
8. **Report is saved** to the `reports/` directory

### Example Output
For the query "Mongol Invasion of Japan in 1274":
```markdown
# Mongol Invasion of Japan in 1274

## Background & Context
- Rise of the Mongol Empire under Kublai Khan...
- Diplomatic demands to Japan (1268-1274)...

## The Invasion: Battle of Bun'ei
- Fleet composition and troop numbers...
- Landing at Hakata Bay...
- Japanese defensive tactics...

## The Typhoon & Retreat
- Weather conditions and their impact...
- Mongol fleet losses...

## Aftermath & Legacy
- Japanese defensive preparations (1275-1281)...
- Second invasion attempt (1281)...
- Cultural impact: the "kamikaze" narrative...

## Sources
1. [Source Title](URL) - accessed YYYY-MM-DD
2. ...
```

## How It Should Work

### User Experience Goals
1. **Simple invocation**: Single command with a query string. No complex configuration for basic use.
2. **Transparent progress**: The system should log which phase it's in (planning, searching, crawling, verifying, writing) so the user knows it's working.
3. **Quality over speed**: A 10-minute well-researched report is preferred over a 1-minute shallow summary.
4. **Graceful degradation**: If SerpAPI key is missing, the system still works with DuckDuckGo + Semantic Scholar. If a URL fails to crawl, it moves on.
5. **Deterministic structure**: Reports always follow the same structural pattern, making them predictable and parseable.

### Key UX Principles
- **No silent failures**: Every tool failure is logged with context.
- **Progressive enhancement**: More API keys = more sources = better reports, but the baseline (zero keys) is fully functional.
- **Session awareness**: Each research run is a named session. Users can reference past sessions.
- **Source transparency**: Every claim in the report links back to its source(s).

## What This Product Is NOT
- Not a chatbot or conversational AI
- Not a real-time search engine
- Not a web scraping framework (Crawl4AI is used as a tool, not the product)
- Not a cloud service — it is intentionally local-only
- Not a general-purpose agent framework — it is purpose-built for deep research

