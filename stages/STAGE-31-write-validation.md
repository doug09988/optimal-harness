# Stage 31 — Write-time Python validation + honest gate counting

Branch: `codex/stage-31-write-validation` from main's tip (554f535).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-31 autopsy, measured 2026-10-28)

Best artifact was hidden-grader GREEN (cycles 4 and 6), yet the gate
counted 11 failures, decomposing to ~4 distinct issues:
1. `test_unit.py` line 1 is a literal markdown fence (```python) —
   the file never compiles, so reports/integration.json is red in
   every cycle. A stage wrote fenced content into a .py deliverable
   and nothing validated it at write time.
2. QA's two wrong-expectation tests (codepoint order, case) keep
   qa.json red; REVIEW echoes one as an unresolved discrepancy.
3. The gate's failures list contains DUPLICATE strings (the
   integration and qa meta entries each appear twice; discrepancy
   entries repeat by criterion), inflating failure_count and
   blurring the ratchet/escalation signals Stages 27-28 cleaned up
   for the acceptance entry.

## The changes (two, both deterministic)

A. **Write-time Python validation.** When a stage writes a file whose
   name ends in .py through the harness's write path (CODER, QA,
   FIXER, INTEGRATOR — wherever deliverable files are materialized),
   the runtime byte-compiles the content (compile() / py_compile
   semantics, no execution). On SyntaxError the write is REJECTED and
   the error (line + message) is returned to the writing stage as a
   retry reason within that stage's EXISTING retry/nudge budget —
   the stage regenerates the file; no new budget is created. If the
   budget exhausts, the stage fails as it does today. Non-.py files
   are untouched. Record a `write_rejected` event (path, error line).
B. **Gate failure dedup.** The gate's failures list is deduplicated
   by exact string, preserving first-seen order, BEFORE counting,
   fingerprinting, ratchet comparison, and best-artifact selection.
   failure_count everywhere (events, escalation, reports) is the
   deduped count. No criterion is removed; only exact-duplicate
   strings collapse.

## Tests

- A fenced or otherwise unparseable .py write is rejected with the
  event, the stage's retry sees the SyntaxError text, and a valid
  rewrite succeeds; a non-.py file with identical content passes.
- Retry-budget exhaustion on repeated bad writes still fails the
  stage (no infinite loop).
- A failures list with exact duplicates counts once; distinct
  strings (incl. near-duplicates differing by one char) count fully.
- Ratchet/escalation unit behavior unchanged on deduped inputs.
- Suite fully green (220 existing + new).

## Acceptance

Suite green; coordinator live run: no syntax-broken .py anywhere in
the run tree, integration no longer red from a fence, gate counts
reflect distinct failures; trajectory reported against verify-31's
18 → 12 → 13 → 11 → 16 → 11.

## Stage 31 implementation

- Added compile-only validation in the shared runtime write/checkpoint path for
  complete `.py` candidates, including appends and writes split into storage
  chunks. Syntax errors reject the candidate before bytes or checkpoints change,
  emit `write_rejected` with path and error line, and return line/message feedback
  through existing stage retry handling. FIXER Python fences reach this validator.
  Non-Python writes retain their existing behavior; no stage prompts, budgets,
  cycle limits, or escalation rules were changed.
- Deduplicated exact gate failure strings in first-seen order before fingerprinting
  and best-artifact selection; counting also deduplicates before applying existing
  bundled acceptance failure/error counts. Gate reports/events expose the same
  failures and count, and all criterion results remain present.
- Added regression coverage for fenced/invalid Python rejection, event payloads,
  retry feedback and recovery, existing retry exhaustion, append preservation,
  non-Python passthrough, and exact versus near-duplicate gate errors. Updated
  byte-preservation fixtures to contain compilable Python comments.
- Verification: `python3 -m unittest discover -s tests`
  Suite result: `Ran 226 tests in 16.072s` / `FAILED (errors=9)`.
  The other 217 tests passed, including all new tests and existing ratchet tests.
  All nine errors are existing HTTP tests whose local server binds fail with
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  No separate loopback/model-server calls were made. Full green verification
  remains blocked by those sandbox socket permissions.
- `git diff --check` passed. Nothing staged or committed.
- Token usage: not reported by the runtime.
