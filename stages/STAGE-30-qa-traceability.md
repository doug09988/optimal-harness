# Stage 30 — QA spec-clause traceability (drop tests that can't cite the SPEC)

Branch: `codex/stage-30-qa-traceability` from main's tip. NOTE: main's
tip is 3fb3b70 PLUS the Stage 29 merge — branch from the CURRENT main
tip in ~/optimal-harness-main, or from the stage-29 branch tip if the
coordinator has not merged yet; check `git log` and branch from the
newest commit that contains Stage 29 (fixer feedback). Do NOT commit
(the coordinator commits). Do NOT run loopback/model calls from the
sandbox. Suite: `python3 -m unittest discover -s tests`.

## Autopsy evidence (verify-30, measured 2026-10-28)

Stage 29's feedback loop worked: the artifact's `return count_words`
bug is fixed and the hidden grader acceptance is GREEN from cycle 2
onward; gate best improved to 11 (campaign best). The run escalated on
a stable QA-side defect instead. Running QA's own test_acceptance.py
(38 tests) against the preserved artifact fails 8, all in one family:
spaces (expects array elements to be SPLIT on whitespace), empty
strings (expects them dropped), unicode (expects normalization
equivalence), mixed case (expects folding), long words. Where the
assertion text was read directly, QA's expectation CONTRADICTS the
spec: the spec says "Count repeated strings exactly, with case
sensitivity" and defines input as a JSON array of strings — no clause
authorizes splitting elements, dropping empties, folding case, or
normalizing unicode. QA is instructed to "derive tests from the SPEC
alone" but embellishes with conventional word-counter assumptions.
The fixer cannot pass tests that forbid spec-conformant behavior, so
the same "defect" repeats until escalation.

## The change (driven QA, plan stage — extends Stage 20's pattern)

1. The QA PLAN call's output format gains a per-test spec citation:
   each planned test entry must include the verbatim SPEC clause it
   derives from (an exact quote from the spec text the QA stage was
   given).
2. The runtime verifies each citation deterministically: the quoted
   text must be a verbatim substring of the spec (normalize only
   whitespace runs for the comparison). A planned test whose citation
   is missing or fails verification is DROPPED before test generation,
   with a `qa_test_dropped` event (test name + reason). Dropped tests
   are not generated, not executed, and never counted as failures.
3. Test-generation calls receive only the sanitized plan (same
   mechanics as Stage 20's plan_entry_dropped).
4. If citation parsing fails wholesale (old-format plan), fall back
   to today's behavior (no drops) and record that in an event — do
   not fail the QA stage.
5. Nothing else changes: hidden grader untouched and independent;
   gate counting, fixer, ratchet, cycle/escalation semantics, budgets
   unchanged.

## Tests

- A plan entry citing a verbatim clause survives; one citing invented
  text is dropped with the event; one with no citation is dropped.
- Whitespace-normalized quotes still verify (spec wrapped across
  lines).
- Dropped tests do not appear in the generated acceptance file.
- Old-format plan (no citations anywhere) → no drops, fallback event.
- Suite fully green (217 existing + new).

## Acceptance

Suite green; coordinator runs the live pipeline: success =
qa_test_dropped events fire live naming the overreach family, the
gate trajectory improves on verify-30's 18 → 11 → 11 → 11 (or the run
closes), and the hidden acceptance stays green. Reported either way.

## Stage 30 implementation report

- Driven QA's PLAN contract now requires `test_name | verbatim SPEC clause`
  entries. Runtime checks citations against the authoritative SPEC with only
  whitespace runs normalized; missing, empty, or non-verbatim citations drop
  the test and emit `qa_test_dropped` with its name and reason.
- Generation receives the sanitized plan. Dropped tests never reach generation,
  assembly, execution, or discrepancy counting. Legacy name-only plans retain
  their previous behavior and emit `qa_plan_citation_fallback`.
- Added three runtime regression tests covering valid and whitespace-normalized
  citations, invented/missing/empty citations, sanitized generation prompts and
  acceptance files, legacy fallback, and an entirely dropped plan.
- Hidden grader, gate counting, fixer, ratchet, cycle/escalation semantics, and
  budgets are unchanged. Nothing was staged or committed.
- Verification command: `python3 -m unittest discover -s tests`
- Suite result: `Ran 220 tests in 15.515s` / `FAILED (errors=9)`.
  All three new tests passed; the nine existing HTTP tests errored because the
  sandbox denied local server binds (`PermissionError: [Errno 1] Operation not
  permitted`). No separate loopback or model-server calls were made, and the
  blocked tests were not retried. Live acceptance remains for the coordinator.
- Token usage: not reported by this session.
