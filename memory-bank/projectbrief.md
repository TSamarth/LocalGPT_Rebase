# Project Brief: A2A Deep Research Pipeline

## Core Idea
A local-first, agent-to-agent (A2A) deep research system. Takes a user query (one-liner or detailed description), runs multi-agent research over web/academic/code sources via a crawl4ai-backed MCP server using local Ollama models, and produces a **structured, vetted markdown report** covering all aspects of the query.

## Core Requirements
- **Input**: user query — one-liner OR detailed description.
- **Clarification brain**: STOP and ask follow-up only on (a) ambiguous scope or (b) missing constraints. Otherwise search autonomously. Clarify before run start only.
- **Adaptive depth**: scale research effort to query complexity (one-liner → shallow, detailed → deep).
- **A2A orchestration**: specialized agents per stage; delegate only at stages requiring it to minimize resource load.
- **Sources via crawl4ai MCP**: open web, academic/papers (arXiv/PDF), code/technical, user-provided URLs/files. Existing: multi-source URL aggregation from SerpAPI + DuckDuckGo + arXiv.
- **Verification**: claim cross-corroborated by 2+ independent sources → keep; claim contradicted → flag (retain both sides, never silently resolve). Every kept claim traceable to source URL(s).
- **Output**: single structured markdown report per session — exec summary → section per sub-topic → sources → contradictions appendix.
- **Human checkpoints**: (1) plan approval before execution, (2) draft approval before final write.

## Hardware Envelope (HARD constraint)
- GPU: RTX 4070 Ti Super — 16 GB VRAM
- CPU: Ryzen 5 3600
- RAM: 16 GB (real bottleneck, not VRAM)
- Storage: unconstrained
- All inference local via Ollama. Only outbound network = source fetching.

## Scope Boundaries
**In**: query intake, clarification logic, planning, source acquisition (crawl4ai MCP), A2A orchestration, verification, markdown report, two checkpoints.
**Out (this phase)**: framework/architecture selection (deferred to design), implementation code, paywalled/authed sources (unless user-seeded), real-time/streaming feeds.

## Source of Truth
This file defines scope. Detailed requirements in [requirements.md]. All other memory-bank files build on this.