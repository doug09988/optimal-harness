# Stage 41 — Adjudication: one judgment field, derived verdict, alias-tolerant parsing

Branch: `codex/stage-41-adjudication-single-field` from main's tip (f65a609).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-40, measured)

Run ESCALATED honestly at best 1 (6 → 7 → 3 → 2 → 1 → 1 → 1);
hidden green in every cycle except cycle 2's genuine aggregation
defect, which the FIXER repaired. The gate's last failure was QA's
`test_single_word_case_sensitive_output` — a genuinely broken test
(it indexes output_data[0] with a tuple). The adjudicator judged
it CORRECTLY six times (3 cycles x 2 attempts): every attempt
returned verdict TEST_WRONG with a derivation blaming the test.
All six were discarded as "incoherent" because the model wrote
the Stage 39 field as `"violation": "test_single_word_case_sensitive_output"`
— near-miss field NAME, and the test's name as the VALUE — instead
of `"violator": "test"`. Stage 39 demanded cross-field consistency
from a redundant field; the model kept the judgment and slipped
the form.

## The change

Single judgment field; the verdict is DERIVED, never also asked:
1. The adjudication response contract becomes
   {"violator": "test" | "artifact" | "uncertain",
    "clause": ..., "derivation": ...}.
   violator "test" → TEST_WRONG; "artifact" → ARTIFACT_WRONG;
   "uncertain" → UNCERTAIN. The model is no longer asked for a
   separate verdict field; if one is present it is ignored.
2. Alias tolerance in parsing, applied in order:
   - `violator` with a value in {test, artifact, uncertain} wins.
   - Else `violation` (the observed alias): value "test" or any
     string starting with "test_" (a test name) maps to "test";
     "artifact" or "implementation" maps to "artifact";
     "uncertain" maps to "uncertain".
   - Else a legacy `verdict` field (TEST_WRONG / ARTIFACT_WRONG /
     UNCERTAIN) maps to the corresponding violator.
   - Else: malformed → Stage 33 UNCERTAIN path.
3. Remove Stage 39's mismatch-retry machinery (there is nothing
   left to mismatch). `test_adjudication_incoherent` is retired;
   incoherence-by-construction is impossible. Eligibility and
   once-per-run semantics stay Stage 33/39: a malformed or
   uncertain outcome does not burn the test's adjudication.
4. The derivation and a verbatim clause remain required for a
   TEST_WRONG quarantine, as before.

## Tests

- The three verbatim live payload shapes from verify-40 (verdict
  TEST_WRONG + "violation": "<the test's own name>" + clause +
  derivation) resolve to TEST_WRONG and quarantine.
- violator-first payloads (each of test/artifact/uncertain)
  derive the right outcomes; a stray verdict field is ignored.
- Legacy verdict-only payloads still work (back-compat with
  Stage 33/38 tests — update those tests to the new contract
  where they assert the old one, keeping their intent).
- Malformed payloads → UNCERTAIN, eligibility preserved.
- Suite fully green (270 existing, updated as needed, + new).

## Acceptance

Suite green; coordinator live run: this test family reaches a
quarantine when the adjudicator's judgment says the test is
wrong, in whatever field shape the model emits it; trajectory
reported against verify-40's best 1. Zero-failure gate pass
(AWAITING_COORDINATOR) is the goal.

## Stage 41 implementation

- Changed the response contract to request only `violator`, `clause`, and
  `derivation`. Verdicts are derived from the judgment, with valid `violator`
  taking precedence over the observed `violation` alias and legacy `verdict`.
- Removed mismatch retries and retired `test_adjudication_incoherent` events.
  Malformed and uncertain outcomes preserve later-cycle eligibility; same-cycle
  attempt limits, completed decisive judgments, and interrupted attempts retain
  their existing once-per-run accounting. Quarantine still requires a verbatim
  SPEC clause and a nonempty, single-line derivation.
- Replaced retry tests with field-precedence, alias, legacy, malformed,
  eligibility, prompt-contract, and candidate-cap coverage. Added the three
  verbatim verify-40 cycle payloads as self-contained regression fixtures.
  Public gate accounting, budgets, discrepancies, and hidden grader paths were
  not changed.
- Verification: `python3 -m unittest discover -s tests`
  reported `Ran 270 tests in 18.713s` and `FAILED (errors=9)`.
  All adjudication tests passed. The nine errors were existing HTTP tests
  failing to bind localhost servers with `PermissionError: [Errno 1] Operation
  not permitted` in this sandbox (one adversarial, one harness, seven model
  tests). No separate loopback/model-server calls were made.
- `git diff --check` passed. Nothing staged or committed.
- Token usage: not reported by the runtime.
