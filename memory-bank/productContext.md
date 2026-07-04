# Product Context

## Why This Exists
Deep research slow, manual: search, open many tabs, read, cross-check, synthesize, cite. Cloud LLM "research" tools leak queries, cost money per run, can't be trusted to verify. This system do deep research **locally and privately** on consumer hardware, verification built in.

## Problems It Solves
- **Trust**: claims cross-corroborated or flagged — no silent hallucination/single-source assertion.
- **Privacy**: all reasoning local (Ollama); only source-fetching touch network.
- **Effort**: one query → one vetted report cover all aspects, no manual tab-juggling.
- **Cost**: no per-token cloud bills; runs on owned hardware.
- **Control**: human checkpoints at plan and draft stages keep user in loop on direction and quality.

## How It Should Work (user view)
1. User submit query (one-liner or detailed).
2. System ask follow-ups ONLY if scope ambiguous or constraints missing.
3. System propose research plan → user approve/edit (Checkpoint 1).
4. System research autonomously: search → crawl → extract → verify.
5. System draft structured markdown → user approve (Checkpoint 2).
6. Final vetted markdown report written to disk.

## UX Goals
- Minimal friction: don't pester; ask only when genuinely blocked.
- Transparency: user see plan and draft before commitment.
- Trustable output: sourced claims, flagged contradictions, no false confidence.
- Predictable resource use: never thrash machine into OOM.