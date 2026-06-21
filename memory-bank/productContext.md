# Product Context

## Why This Exists
Deep research is slow and manual: search, open many tabs, read, cross-check, synthesize, cite. Cloud LLM "research" tools leak queries, cost money per run, and can't be trusted to verify. This system does deep research **locally and privately** on consumer hardware, with verification built in.

## Problems It Solves
- **Trust**: claims are cross-corroborated or flagged — no silent hallucination/single-source assertion.
- **Privacy**: all reasoning local (Ollama); only source-fetching touches network.
- **Effort**: one query → one vetted report covering all aspects, no manual tab-juggling.
- **Cost**: no per-token cloud bills; runs on owned hardware.
- **Control**: human checkpoints at plan and draft stages keep the user in the loop on direction and quality.

## How It Should Work (user view)
1. User submits query (one-liner or detailed).
2. System asks follow-ups ONLY if scope ambiguous or constraints missing.
3. System proposes a research plan → user approves/edits (Checkpoint 1).
4. System researches autonomously: search → crawl → extract → verify.
5. System drafts structured markdown → user approves (Checkpoint 2).
6. Final vetted markdown report written to disk.

## UX Goals
- Minimal friction: don't pester; ask only when genuinely blocked.
- Transparency: user sees the plan and the draft before commitment.
- Trustable output: sourced claims, flagged contradictions, no false confidence.
- Predictable resource use: never thrash the machine into OOM.