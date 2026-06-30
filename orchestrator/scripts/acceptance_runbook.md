# E5.S2 Live Acceptance Runbook

All commands run from the `orchestrator/` directory unless stated otherwise.
Each step must be completed in order. Do not skip preflight.

---

## Prerequisites

- Ollama is running (`ollama serve` or the system service).
- Models pulled:
  ```
  ollama pull qwen2.5:14b
  ollama pull nomic-embed-text
  ```
- `scripts/check_setup.py` exits 0 (see Step 1).
- The project virtual environment is activated or you prefix commands with `uv run`.

---

## Step 1: Preflight

Verify Ollama reachability, model presence, and VRAM headroom before any live run.

```bash
.venv\Scripts\python.exe scripts/check_setup.py
```

Expected: all checks green, no errors. If any check fails, resolve it before continuing.

---

## Step 2: Offline Baseline (must stay green throughout)

Run the full offline suite. This must remain green at all times — live tests are additive and opt-in via `-m live`.

```bash
.venv\Scripts\python.exe -m pytest tests/ -q \
  -k "not (test_ac1 or test_ac2 or test_resume)" \
  --ignore=tests/test_live_a2a_roundtrip.py
```

Expected: 232+ tests collected, 0 failures, 0 errors.

Record the count: `______ passed`

If any offline tests regress, stop. Do not run live tests against a broken baseline.

---

## Step 3: T1 — AC1–AC7 Live Acceptance

Runs the seeded-corpus acceptance harness against the real `qwen2.5:14b` model.

```bash
.venv\Scripts\python.exe -m pytest -m live tests/test_live_acceptance.py -v
```

This test:
- Drives real model inference via the resume harness (CP1/CP2 answered from scripted replies).
- Injects seeded source corpus through the `build_research_workflow(acquirer_node=, extractor_node=)` DI seam for AC4/AC5/AC7.
- Grades all 7 ACs deterministically against the `ClaimLedger` and rendered markdown.
- Prints a per-AC evidence table at the end.

Expected output includes a table like:
```
AC1  PASS  clarifier fired on ambiguous / skipped on clear
AC2  PASS  CP1 and CP2 blocked on adk_request_input
AC3  PASS  all 4 markdown sections present
AC4  PASS  contradiction in ledger + Factual Contradictions section
AC5  PASS  lone claim dropped / marked uncorroborated
AC6  PASS  (see T4 below)
AC7  PASS  temporal_drift type + Temporal Drift section
```

Paste the evidence table into the Evidence Table at the end of this runbook.

---

## Step 4: T2 — Live Resume

Tests cross-process resume after a forced mid-loop kill.

```bash
.venv\Scripts\python.exe -m pytest -m live tests/test_live_resume.py -v
```

This test:
- Starts a full run against the real model.
- Sends SIGTERM/kills the process mid-loop.
- Resumes using the persisted `invocation_id` and `SESSION_DB_URL`.
- Asserts that already-completed nodes are skipped (body-counter or DB-state proof) and the run reaches terminal state.

Expected: 1 live test passes, resume log shows skipped nodes.

Paste the resume evidence (skipped-node log excerpt) into the Evidence Table.

---

## Step 5: T3 — Live A2A Round-Trip

Requires two terminals. Start the server first.

**Terminal 1 — start the server:**
```bash
.venv\Scripts\python.exe -m app.server
```

Wait until you see `Uvicorn running on http://0.0.0.0:8001`.

**Terminal 2 — run the A2A tests:**
```bash
.venv\Scripts\python.exe -m pytest -m live tests/test_live_a2a_roundtrip.py -v
```

This test:
- Fetches the agent card from `http://localhost:8001/.well-known/agent.json`.
- Drives a full CP1 → CP2 (and deep CP1 → CP3 → CP2) HITL session over A2A JSON-RPC.
- Asserts the run reaches a terminal ledger and draft.

Expected: 2 live tests pass (card fetch + full HITL round-trip).

Stop the server (Ctrl-C in Terminal 1) after the tests complete.

---

## Step 6: T4 — OOM/VRAM Budget

Run the memory probe around a real full-depth query. Requires T1 green first (proves the pipeline runs to completion).

```bash
.venv\Scripts\python.exe scripts/mem_probe.py -- \
  .venv\Scripts\python.exe main.py \
  "What are the key architectural differences between Tokio and async-std for high-throughput Rust services?"
```

The probe:
- Samples subprocess RSS every 2 seconds until exit.
- Calls `ollama ps` on exit to capture VRAM.
- Prints a summary with PASS/FAIL against the 16 GB RAM and 14 GB VRAM thresholds.
- Exits 0 only if both thresholds pass.

Expected output (example):
```
Peak RAM : 4.31 GB
VRAM     : 9.2 GB

ollama ps VRAM snapshot:
NAME              ID              SIZE      PROCESSOR    UNTIL
qwen2.5:14b       abc123def456    9.2 GB    100% GPU     ...

RAM  PASS/FAIL : PASS  (4.31 GB < 16.0 GB)
VRAM PASS/FAIL : PASS  (9.2 GB < 14.0 GB)
```

Paste the full probe output into the Evidence Table.

---

## Evidence Table

Fill in after each step. Do not mark a row green without pasting the actual output.

| Check | Command / Source | Result | Evidence (paste or ref) |
|-------|-----------------|--------|------------------------|
| Preflight | `check_setup.py` | PASS / FAIL | |
| Offline baseline | `pytest tests/ -q ...` | `______ passed` | |
| AC1 clarify trigger | T1 evidence table | PASS / FAIL | |
| AC2 CP1+CP2 block | T1 evidence table | PASS / FAIL | |
| AC3 markdown sections | T1 evidence table | PASS / FAIL | |
| AC4 contradiction flagged | T1 evidence table | PASS / FAIL | |
| AC5 unverified dropped | T1 evidence table | PASS / FAIL | |
| AC7 temporal drift | T1 evidence table | PASS / FAIL | |
| T2 live resume | `test_live_resume.py` | PASS / FAIL | |
| T3 A2A card fetch | `test_live_a2a_roundtrip.py` | PASS / FAIL | |
| T3 A2A full HITL | `test_live_a2a_roundtrip.py` | PASS / FAIL | |
| T4 RAM < 16 GB | `mem_probe.py` | PASS / FAIL | |
| T4 VRAM < 14 GB | `mem_probe.py` | PASS / FAIL | |

---

## Pass/Fail Criteria

All of the following must be green for E5.S2 to be done and the migration to be complete.

- [ ] AC1: clarifier fires on ambiguous query, skips on clear query
- [ ] AC2: CP1 and CP2 block on `adk_request_input`
- [ ] AC3: markdown draft contains exec summary, per-subtopic, sources, contradictions+Temporal Drift sections
- [ ] AC4: planted cross-source contradiction flagged in ledger and Factual Contradictions section
- [ ] AC5: single-source unverified claim dropped or marked uncorroborated
- [ ] AC7: temporal drift >= 18 months produces `type==temporal_drift` in ledger and appears in Temporal Drift section (not Factual)
- [ ] T2 resume: mid-loop kill -> resume -> skipped already-completed nodes -> run completes
- [ ] T3 A2A: agent card at `/.well-known/agent.json`; full CP1->CP2 HITL over A2A to terminal ledger/draft
- [ ] T4 RAM: peak RSS < 16 GB
- [ ] T4 VRAM: `ollama ps` VRAM column < 14 GB
- [ ] Offline suite: 232+ tests green, 0 regressions

Epic gate: all boxes checked -> E5.S2 done -> v1->v2 ADK 2.x migration complete.
