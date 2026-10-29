# Stage 33 — Failing-test adjudication and quarantine

Branch: `codex/stage-33-test-adjudication` from main's tip (8ba7e47).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-33 autopsy, measured 2026-10-28)

The pipeline now produces a spec-correct artifact (hidden acceptance
green in cycles 1 and 3; both deliverable .py files compile; fences
auto-repaired). ALL residual gate redness is model-authored tests:
- QA's test_output_unicode_sorting expects Greek-before-Latin in
  "ascending Unicode code-point order" — mathematically wrong
  ('z'=122 < 'α'=945); its case-sensitivity variant likewise
  contradicts the spec's plain text.
- The CODER's unit tests invoke the tool wrongly (one runs the JSON
  input file AS the Python script and blames the artifact for the
  exit code) — 9 of 13 fail/error against a correct artifact.
Nothing in the harness verifies the TESTS; the fixer is told to fix
the artifact until wrong tests pass, which is impossible, and the
run escalates on schedule. Human teams resolve this by adjudicating
the test against the spec. That is this stage.

## The change (cycle loop, between gate evaluation and FIXER)

1. **Eligibility.** A test in a model-authored suite (QA acceptance
   file, project unit tests — NOT the hidden grader, which is never
   adjudicated) that FAILS in 2 consecutive gate cycles (same suite
   + test name) becomes eligible for adjudication, once per run.
2. **Adjudication call.** Before the next cycle's FIXER stage, the
   runtime makes ONE driven call per eligible test (max 3 per
   cycle, charged to the cycle budget). Inputs: the SPEC text, the
   test's source, its cited spec clause when one exists (Stage 30),
   and the latest failure text (assertion diff) for that test.
   Required output: a verdict — TEST_WRONG (must include the verbatim
   spec clause and a one-line derivation of why the expectation
   contradicts it), ARTIFACT_WRONG, or UNCERTAIN.
3. **Quarantine.** On TEST_WRONG the test is quarantined for the
   rest of the run: excluded from its suite's gate accounting (the
   suite report carries a `quarantined` list with the derivation),
   a `test_quarantined` event fires (suite, test, clause), and the
   next FIXER/QA briefs note the quarantine so the model stops
   chasing it. ARTIFACT_WRONG and UNCERTAIN change nothing. A
   malformed adjudication response counts as UNCERTAIN.
4. Quarantine never applies to the hidden grader, never deletes the
   test file content (the test still exists and runs in the suite's
   own report — it is excluded from GATE accounting only, and the
   exclusion is disclosed in the report and events).
5. Ratchet/best-artifact comparisons use the gate accounting
   consistently (quarantined tests excluded in all compared cycles'
   recounts is NOT required — apply quarantine prospectively from
   the cycle it is granted; note this in the report).

## Tests

- A test failing cycles N and N+1 is adjudicated before cycle N+2's
  fixer; a test failing non-consecutively is not.
- TEST_WRONG verdict (with clause + derivation) quarantines: gate
  count drops accordingly, event fires, suite report lists it.
- ARTIFACT_WRONG / UNCERTAIN / malformed responses: no quarantine.
- Cap of 3 adjudications per cycle and once-per-test-per-run hold.
- Hidden grader tests are never eligible.
- Suite fully green (229 existing + new).

## Acceptance

Suite green; coordinator live run: adjudication fires on the
persistent QA pair (codepoint/case), quarantine events and gate
accounting reflect the verdicts, trajectory reported against
verify-33's deduped 4 → 6 → 4 (best 4, all four entries test-side).

## Stage 33 implementation

Implemented persistent, project-scoped tracking of consecutive failures by public
suite and qualified test name. Eligible tests are adjudicated before the following
FIXER runs, with at most three attempts per cycle and one attempt per test per run.
Calls use the existing FIXER ledger and durable usage meter; budget limits and
escalation rules are unchanged. Prompts carry the SPEC, test source, latest failure
text, and the QA citation when available. Invalid responses become UNCERTAIN;
TEST_WRONG requires an actual verbatim SPEC substring and a one-line derivation.

Quarantine events record suite, test, clause, and derivation. Public suite reports
retain the original failures and disclose quarantined tests and remaining gate
failures. Gate counts, fingerprints, and artifact comparisons use prospective
accounting; earlier cycles are not recounted. Test files and command receipts
remain intact. FIXER and QA prompts receive quarantine notes. Hidden-grader checks
and the hidden-grader path are unchanged.

Added 11 tests covering consecutive and interrupted eligibility, verdict validation,
once-per-run and per-cycle limits, shared metering, citations, durable project
isolation, source lookup, private-check exclusion, incomplete output, and complete
and partial quarantine accounting.

Verification: `python3 -m unittest discover -s tests`

Suite result line: `Ran 240 tests in 16.128s` — `FAILED (errors=9)`.
All 11 new tests passed; 229 of 240 tests passed overall. The nine errors are
existing HTTP/socket tests blocked by this sandbox with
`PermissionError: [Errno 1] Operation not permitted` while binding localhost.
No separate loopback/model-server calls were made. `git diff --check` passed.
Nothing was staged or committed.

Token usage: not reported by the execution environment.
