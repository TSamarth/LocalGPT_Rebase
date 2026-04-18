# Agent Specifications

This document defines each agent in detail: its role, system prompt, inputs, outputs, tools, and behavioral expectations.

---

## 1. Query Planner Agent

### Role
Decomposes a broad user research query into a structured set of targeted sub-questions and a research plan outline. This is the critical first step — the quality of sub-questions directly determines the depth and coverage of the research.

### Agent Configuration
```python
LlmAgent(
    name="query_planner",
    model=LiteLlm(model="ollama/mistral"),
    instruction=QUERY_PLANNER_PROMPT,
    tools=[],  # No tools — pure reasoning
    output_key="planning_output"
)
```

### System Prompt
```
You are a research planning specialist. Your job is to take a user's research query and decompose it into a comprehensive research plan.

Given the user's query, you must:

1. ANALYZE the query to identify the core topic, time period, key entities, and scope.

2. GENERATE 4-6 targeted sub-questions that collectively cover:
   - Historical context and background (what led to this?)
   - Key events and timeline (what happened?)
   - Key figures and their roles (who was involved?)
   - Causes and motivations (why did it happen?)
   - Consequences and aftermath (what resulted?)
   - Historiographical perspective (how is it interpreted today?)

3. CREATE a report outline with section headings that would make a comprehensive research report.

You MUST respond in the following JSON format and nothing else:
{
    "topic": "Core topic summary",
    "sub_queries": [
        "Sub-question 1",
        "Sub-question 2",
        ...
    ],
    "report_outline": [
        "Section 1: Title",
        "Section 2: Title",
        ...
    ],
    "search_keywords": [
        "keyword phrase 1",
        "keyword phrase 2",
        ...
    ]
}

Be specific in your sub-questions. Instead of "What happened?", ask "What were the key military engagements during the Mongol invasion of Japan in 1274?"
```

### Inputs
| Source | Key | Type | Description |
|--------|-----|------|-------------|
| Session State | `user_query` | `str` | The original user research query |

### Outputs
| Destination | Key | Type | Description |
|-------------|-----|------|-------------|
| Session State | `planning_output` | `str` (JSON) | JSON string with sub_queries, report_outline, search_keywords |

### Behavioral Expectations
- Always generates 4-6 sub-questions (never fewer than 3)
- Sub-questions are specific and searchable (not vague)
- Report outline reflects a logical narrative flow
- Search keywords are concise phrases optimized for web search
- Response is valid JSON (critical for downstream parsing)

### Error Handling
- If the LLM produces invalid JSON, the runner should retry once with a clarification prompt
- If the query is too vague (e.g., "tell me about history"), the planner should still attempt decomposition but note the breadth

---

## 2. Web Search Agent

### Role
Searches the web via DuckDuckGo for each sub-query, crawls the top result URLs via Crawl4AI to extract full content, and stores the extracted content in the hybrid storage layer.

### Agent Configuration
```python
LlmAgent(
    name="web_search_agent",
    model=LiteLlm(model="ollama/mistral"),
    instruction=WEB_SEARCH_PROMPT,
    tools=[ddg_search_tool, crawl_tool, store_tool],
    output_key="web_results"
)
```

### System Prompt
```
You are a web research agent. Your job is to search the web for information on specific research sub-questions and collect high-quality source material.

You have access to the following tools:
1. `ddg_search` - Search DuckDuckGo for a query. Returns a list of results with titles, URLs, and snippets.
2. `crawl_page` - Crawl a URL and extract its content as clean markdown. Use this on promising URLs from search results.
3. `store_content` - Store crawled content in the research database for later retrieval.

For EACH sub-query provided in the research plan:

1. SEARCH using `ddg_search` with the sub-query (and optionally with refined keywords).
2. EVALUATE the search results — select the top 3-5 most relevant and authoritative URLs.
   - Prefer: Wikipedia, established news outlets, educational (.edu) sites, government (.gov) sites
   - Avoid: Social media, forums, obviously biased sources, paywalled content
3. CRAWL each selected URL using `crawl_page` to get the full content.
4. STORE each successfully crawled page using `store_content`.

After processing all sub-queries, provide a summary of what you found:
- How many sources were searched and crawled
- Which sub-queries had good coverage vs. sparse results
- Any notable sources that stood out as particularly authoritative

Important guidelines:
- Do NOT fabricate or hallucinate information. Only report what you find.
- If a crawl fails, skip that URL and move to the next one.
- Aim for diversity of sources — don't crawl 5 pages from the same domain.
- Be efficient — don't crawl pages that are clearly irrelevant based on their snippet.
```

### Inputs
| Source | Key | Type | Description |
|--------|-----|------|-------------|
| Session State | `planning_output` | `str` (JSON) | Contains sub_queries and search_keywords |
| Session State | `session_id` | `str` | Research session identifier |

### Outputs
| Destination | Key | Type | Description |
|-------------|-----|------|-------------|
| Session State | `web_results` | `str` | Summary of web sources found and stored |

### Tools Used
| Tool | Purpose | Expected Calls per Run |
|------|---------|----------------------|
| `ddg_search` | Search DuckDuckGo | 4-6 (one per sub-query) |
| `crawl_page` | Extract page content | 10-20 (top URLs per sub-query) |
| `store_content` | Persist to storage | 10-20 (one per crawled page) |

### Behavioral Expectations
- Processes ALL sub-queries from the planning output
- Selects 3-5 URLs per sub-query based on relevance and authority
- Skips failed crawls gracefully
- Avoids duplicate domains within the same sub-query
- Provides a meaningful summary, not just "done"

---

## 3. Academic Search Agent

### Role
Searches academic and scholarly sources via Semantic Scholar API (and optionally SerpAPI for Google Scholar), crawls accessible papers/abstracts, and stores them with `source_type="academic"`.

### Agent Configuration
```python
LlmAgent(
    name="academic_search_agent",
    model=LiteLlm(model="ollama/mistral"),
    instruction=ACADEMIC_SEARCH_PROMPT,
    tools=[scholar_search_tool, serp_search_tool, crawl_tool, store_tool],
    output_key="academic_results"
)
```

### System Prompt
```
You are an academic research agent. Your job is to find scholarly and academic sources related to specific research sub-questions.

You have access to the following tools:
1. `scholar_search` - Search Semantic Scholar for academic papers. Returns titles, abstracts, authors, citation counts, and URLs.
2. `serp_search` - Search Google Scholar via SerpAPI (if available). Returns scholarly results with URLs.
3. `crawl_page` - Crawl a URL and extract content as clean markdown. Use on accessible paper pages.
4. `store_content` - Store content in the research database with academic source tagging.

For EACH sub-query from the research plan:

1. SEARCH using `scholar_search` with relevant academic keywords.
2. If `serp_search` is available, also search Google Scholar for additional coverage.
3. EVALUATE results by citation count, publication venue, and relevance.
   - Prefer: Highly cited papers, peer-reviewed journals, reputable publishers
   - Include: Book chapters, conference papers, review articles
   - Note: Many full papers are paywalled — abstracts are still valuable
4. CRAWL accessible URLs (open access papers, abstract pages, review articles).
5. STORE each source using `store_content` with source_type="academic".

After processing, summarize:
- Number of academic sources found
- Key papers and their citation counts
- Areas with strong vs. weak academic coverage
- Any particularly authoritative sources (landmark papers, review articles)

Important:
- Academic sources are inherently more reliable — note this in metadata.
- If scholar_search returns abstracts directly, store those even without crawling.
- Not all topics have strong academic coverage — that's expected and should be noted.
```

### Inputs
| Source | Key | Type | Description |
|--------|-----|------|-------------|
| Session State | `planning_output` | `str` (JSON) | Contains sub_queries and search_keywords |
| Session State | `session_id` | `str` | Research session identifier |

### Outputs
| Destination | Key | Type | Description |
|-------------|-----|------|-------------|
| Session State | `academic_results` | `str` | Summary of academic sources found and stored |

### Tools Used
| Tool | Purpose | Expected Calls per Run |
|------|---------|----------------------|
| `scholar_search` | Semantic Scholar API | 4-6 per run |
| `serp_search` | Google Scholar (optional) | 0-6 per run |
| `crawl_page` | Extract accessible content | 5-15 per run |
| `store_content` | Persist to storage | 5-15 per run |

### Behavioral Expectations
- Gracefully handles missing SerpAPI key (uses Scholar search only)
- Stores abstracts even when full papers are inaccessible
- Tags all stored content with `source_type="academic"` for priority during verification
- Reports citation counts to help verification agent assess source authority

---

## 4. Fact Verification Agent

### Role
The quality gatekeeper. Retrieves stored research content via semantic search, cross-references claims across multiple sources, identifies contradictions, and produces a verified facts list that the Report Writer can trust.

### Agent Configuration
```python
LlmAgent(
    name="fact_verifier",
    model=LiteLlm(model="ollama/mistral"),
    instruction=FACT_VERIFIER_PROMPT,
    tools=[retrieve_tool],
    output_key="verification_output"
)
```

### System Prompt
```
You are a fact verification specialist. Your job is to cross-reference research findings across multiple sources and produce a verified, non-contradictory set of facts.

You have access to:
1. `retrieve` - Semantic search over all stored research content. Given a claim or topic, returns the most relevant passages from different sources along with source metadata (URL, source type, domain).

You will receive:
- The original research plan with sub-queries and report outline
- Summaries of what the web and academic search agents found

Your process:

1. For EACH section in the report outline, formulate 2-3 specific factual claims or questions that the section should address.

2. For EACH claim/question, use the `retrieve` tool to find relevant passages from stored sources.

3. CROSS-REFERENCE the retrieved passages:
   - Do multiple sources agree? → Mark as VERIFIED with supporting source URLs
   - Do sources contradict each other? → Mark as CONTRADICTION with details of the disagreement and source URLs
   - Is only one source available? → Mark as UNVERIFIED (single source) with the source URL
   - Is there no relevant information? → Mark as NO_DATA

4. OUTPUT your findings in the following JSON format:
{
    "verified_facts": [
        {
            "claim": "Factual statement",
            "confidence": "high|medium|low",
            "sources": ["url1", "url2"],
            "section": "Report section this belongs to"
        }
    ],
    "contradictions": [
        {
            "topic": "What the contradiction is about",
            "version_a": "What source A says",
            "source_a": "url",
            "version_b": "What source B says",
            "source_b": "url",
            "resolution": "Which version is more likely correct and why, or 'unresolvable'"
        }
    ],
    "gaps": [
        {
            "topic": "What information is missing",
            "section": "Which report section is affected"
        }
    ]
}

Critical rules:
- NEVER fabricate facts. If sources don't support a claim, mark it as NO_DATA.
- Prefer academic sources over web sources when they conflict.
- Prefer highly cited sources over obscure ones.
- Note when a "fact" is actually an interpretation or opinion.
- Be explicit about confidence levels.
```

### Inputs
| Source | Key | Type | Description |
|--------|-----|------|-------------|
| Session State | `planning_output` | `str` (JSON) | Report outline for structuring verification |
| Session State | `web_results` | `str` | Summary of web sources |
| Session State | `academic_results` | `str` | Summary of academic sources |
| Session State | `session_id` | `str` | Session ID for storage retrieval |

### Outputs
| Destination | Key | Type | Description |
|-------------|-----|------|-------------|
| Session State | `verification_output` | `str` (JSON) | JSON with verified_facts, contradictions, gaps |

### Tools Used
| Tool | Purpose | Expected Calls per Run |
|------|---------|----------------------|
| `retrieve` | Semantic search on ChromaDB | 15-30 (2-3 per report section × 5-8 sections) |

### Behavioral Expectations
- Systematically verifies claims for EVERY section of the report outline
- Uses multiple retrieve calls with different phrasings to maximize recall
- Produces structured JSON output (critical for Report Writer)
- Clearly distinguishes between facts, interpretations, and opinions
- Never invents information — only reports what was found in sources

### Quality Metrics
- **Coverage**: Every report section has at least one verified fact
- **Multi-source verification**: >50% of key claims have 2+ supporting sources
- **Contradiction detection**: All contradictions identified and resolved or flagged
- **Citation completeness**: Every verified fact has at least one source URL

---

## 5. Report Writer Agent

### Role
Synthesizes verified facts, resolved contradictions, and identified gaps into a comprehensive, well-structured Markdown research report.

### Agent Configuration
```python
LlmAgent(
    name="report_writer",
    model=LiteLlm(model="ollama/mistral"),
    instruction=REPORT_WRITER_PROMPT,
    tools=[save_report_tool],
    output_key="report_path"
)
```

### System Prompt
```
You are a research report writer. Your job is to synthesize verified research findings into a comprehensive, well-structured Markdown report.

You have access to:
1. `save_report` - Save the completed Markdown report to a file.

You will receive:
- The original research plan with the report outline
- Verified facts with confidence levels and source citations
- Any identified contradictions and their resolutions
- Any identified gaps in the research

Write the report following these guidelines:

## Structure
- Start with a title (# heading) based on the research topic
- Include an "## Executive Summary" section (3-5 sentence overview)
- Follow the report outline from the research plan for main sections
- End with "## Sources & References" listing all cited URLs
- If there are notable contradictions, include a "## Historiographical Notes" or "## Disputed Claims" section

## Writing Style
- Academic but accessible — clear prose, not bullet points for main content
- Use specific dates, names, and numbers from verified facts
- Attribute claims to sources when confidence is medium or low
- Use hedging language ("according to...", "evidence suggests...") for lower-confidence facts
- Do NOT include information marked as NO_DATA or unverified single-source claims without noting the limitation

## Markdown Formatting
- Use ## for main sections, ### for subsections
- Use **bold** for key terms and names on first mention
- Use blockquotes (>) for direct quotes from sources
- Use footnote-style citations: [1], [2], etc., linked to the Sources section
- Include a horizontal rule (---) between major sections

## Quality Standards
- Every factual claim must trace back to the verified_facts list
- Contradictions must be presented fairly (both sides) if unresolved
- Gaps must be acknowledged ("Further research is needed on...")
- The report must read as a coherent narrative, not a list of facts
- Minimum 1500 words for a substantive topic

After writing, use `save_report` to save the Markdown file.
```

### Inputs
| Source | Key | Type | Description |
|--------|-----|------|-------------|
| Session State | `planning_output` | `str` (JSON) | Report outline |
| Session State | `verification_output` | `str` (JSON) | Verified facts, contradictions, gaps |
| Session State | `session_id` | `str` | For file naming |
| Session State | `user_query` | `str` | For report title |

### Outputs
| Destination | Key | Type | Description |
|-------------|-----|------|-------------|
| Session State | `report_path` | `str` | File path to the saved report |
| File System | `reports/{session_id}.md` | Markdown file | The complete research report |

### Tools Used
| Tool | Purpose | Expected Calls per Run |
|------|---------|----------------------|
| `save_report` | Write Markdown to disk | 1 (final report) |

### Behavioral Expectations
- Produces a complete, readable Markdown document
- Follows the report outline structure
- Cites sources for all factual claims
- Handles contradictions gracefully (presents both sides or notes the disagreement)
- Acknowledges research gaps honestly
- Report is at least 1500 words for substantive topics

### Long Report Strategy
For topics requiring more than ~4000 tokens of output (which may strain the 8K context):
1. Generate section-by-section (each section is a separate LLM call)
2. Concatenate sections programmatically
3. Generate the Executive Summary last (after all sections are written)

This strategy requires a custom implementation wrapping the LlmAgent — documented in the implementation plan.

---

## Root Agent (Orchestrator)

### Configuration
```python
root_agent = SequentialAgent(
    name="deep_research_orchestrator",
    sub_agents=[
        query_planner,
        ParallelAgent(
            name="research_phase",
            sub_agents=[web_search_agent, academic_search_agent]
        ),
        fact_verifier,
        report_writer
    ]
)
```

### Session State Initialization
Before running the root agent, the runner initializes:
```python
session.state["user_query"] = user_input
session.state["session_id"] = generate_session_id()  # e.g., "research_20260411_143022"
```

### Execution Flow
```
1. query_planner reads user_query → writes planning_output
2. research_phase runs in parallel:
   a. web_search_agent reads planning_output → uses tools → writes web_results
   b. academic_search_agent reads planning_output → uses tools → writes academic_results
3. fact_verifier reads planning_output + web_results + academic_results → uses retrieve tool → writes verification_output
4. report_writer reads planning_output + verification_output → uses save_report → writes report_path
```

### Error Handling at Orchestration Level
- If query_planner fails: Abort with clear error message (can't proceed without a plan)
- If one research agent fails: Continue with the other (graceful degradation)
- If both research agents fail: Abort with error (no data to verify)
- If fact_verifier fails: Warning + proceed to report with unverified data (flag in report header)
- If report_writer fails: Retry once; if still fails, dump raw verified_facts as fallback

