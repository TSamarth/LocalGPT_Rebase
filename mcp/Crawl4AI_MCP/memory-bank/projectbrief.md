# Project Brief: Crawl4AI FastMCP Research Server

## Project Name
crawl4ai-mcp — Crawl4AI FastMCP Server for Agentic Deep Research

## Core Purpose
A locally-hosted MCP (Model Context Protocol) server that exposes Crawl4AI's web crawling capabilities as structured tools for consumption by a Google ADK (Agent Development Kit) agentic research framework.

## Primary Users
- Google ADK Research Agent (automated — not human users)
- Discovery/Triage Agent (Google ADK sub-agent for URL evaluation)

## Core Goals
1. Enable an AI agent to autonomously crawl, triage, and extract high-quality research content from the web
2. Provide multiple crawl strategies (adaptive, BFS deep, batch) selectable per-URL based on content type
3. Store crawled content in a hybrid ChromaDB (semantic) + SQLite (structured) storage layer
4. Expose semantic search over stored content for research synthesis

## Key Constraints
- **Transport:** stdio only (local, no OAuth needed)
- **LLM Provider:** Ollama only (local, no cloud API keys)
- **Embedding Model:** nomic-embed-text (via Ollama)
- **Python Version:** 3.11+
- **Package Manager:** uv

## Success Criteria
- All 8 MCP tools functional and tested
- Full pipeline: discover → triage → crawl → chunk → embed → store → search
- ADK agent can execute a full research workflow via MCP tool calls
- ChromaDB + SQLite hybrid storage persists across sessions

