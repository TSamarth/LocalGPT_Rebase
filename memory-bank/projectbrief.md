# Project Brief: LocalGPT Deep Research Agent

## Project Name
**LocalGPT Deep Research Agent** (codename: `LocalGPT_Rebase`)

## Vision Statement
A fully local, privacy-first deep research agentic framework that runs entirely on consumer-grade hardware, capable of autonomously researching any user topic and producing comprehensive, factually verified Markdown reports.

## Core Requirements

### Functional Requirements
1. **Deep Query Research**: Accept any user topic/query (e.g., "Mongol Invasion of Japan in 1274") and produce an exhaustive research report.
2. **Multi-Source Information Gathering**: Acquire information from multiple independent sources:
   - DuckDuckGo web search (free, no API key required)
   - SerpAPI / Google Scholar (optional, API key enhanced)
   - Semantic Scholar API (free, academic focus)
   - Direct web page crawling via Crawl4AI
3. **Fact Verification**: Cross-reference all claims across sources. No conflicting, dubious, or unsupported information in the final report.
4. **Structured Report Generation**: Output a well-organized Markdown report with sections (Background, Main Events, Aftermath, Sources/Citations, etc.).
5. **Data Persistence**: Store crawled and processed research data in a hybrid storage layer (vector + relational) for retrieval, deduplication, and future reuse.

### Non-Functional Requirements
1. **Fully Local Execution**: All LLM inference, embedding, crawling, and storage run on local hardware. No cloud LLM APIs required.
2. **Hardware Constrained**: Must operate within:
   - GPU: NVIDIA RTX 3070 (8GB VRAM)
   - CPU: AMD Ryzen 5 3600 (6 cores / 12 threads)
   - RAM: 16 GB DDR4
3. **Reasonable Latency**: Full research cycle (plan → search → crawl → verify → write) should complete within 5–15 minutes depending on query complexity.
4. **Extensibility**: Architecture must allow adding new search sources, agents, or tools without restructuring.
5. **Reproducibility**: Given the same query and source availability, reports should be consistent in structure and factual content.

## Target Users
- Researchers who need comprehensive topic summaries
- Students needing well-sourced academic overviews
- Professionals requiring competitive intelligence or market research
- Privacy-conscious users who cannot send data to cloud APIs

## Success Criteria
1. Given a historical query like "Mongol Invasion of Japan in 1274", the system produces a report covering causes, events, key figures, aftermath, and long-term impact — with zero factual contradictions.
2. The system uses at least 2 independent source types (web + academic) per research run.
3. The entire pipeline runs locally without internet-dependent LLM APIs.
4. Peak RAM usage stays under 12GB (leaving headroom for OS and other processes).
5. Reports include proper source citations with URLs.

## Constraints & Boundaries
- **In Scope**: Research report generation, multi-source search, web crawling, fact verification, local LLM inference, vector + relational storage.
- **Out of Scope**: Real-time chat interface, GUI/web frontend (CLI-first), multi-language report generation (English only for v1), PDF export (Markdown only for v1).

## Project Timeline
- **Phase 1**: Documentation & Architecture (current)
- **Phase 2**: Storage Layer + Tools Implementation
- **Phase 3**: Agent Implementation + Orchestration
- **Phase 4**: Integration Testing + Prompt Tuning
- **Phase 5**: End-to-End Testing + Optimization

