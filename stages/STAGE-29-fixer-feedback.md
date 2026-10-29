# Stage 29 — FIXER execution feedback loop (within-cycle)

Branch: `codex/stage-29-fixer-feedback` from main's tip (3fb3b70). Do
NOT commit (the coordinator commits). Do NOT run loopback/model calls
from the sandbox; live verification is the coordinator's job. Suite:
`python3 -m unittest discover -s tests`.

## Diagnosis (verify-29, measured, 2026-10-28)

The preserved best artifact (wordfreq.py) crashes on EVERY success-path
input: `count_words` ends with `return count_words` (returns the
function object) instead of `return counts`, so main() dies with
AttributeError: 'function' object has no attribute 'items'. Error-path
tests pass (they exit 2 before reaching it). The two persistent gate
failures — test_aggregation_and_sort (5/5 cycles) and test_empty (4/5) —
are exactly the success-path cases. QA's own acceptance suite in the
work dir (test_acceptance.py, 38 tests) fails 31/38 against this
artifact: the execution truth exists inside the run, but the driven
FIXER writes corrected files BLIND — it never runs anything, and its
feedback arrives a full cycle later, diluted (QA regenerates, briefs
summarize). The fixer kept rewriting around the traceback's surface
line (sorted(...) in main) and re-deriving equivalent code.

## The change (driven FIXER cycle, engine/verifier seam)

After the driven FIXER's items complete for a cycle and BEFORE the
cycle's gate evaluation:

1. The runtime executes the run's own QA acceptance file
   (test_acceptance.py in the work dir — the QA artifact; NEVER the
   hidden grader file, which the fixer must not learn from) against
   the fixed artifact, using the existing command-runner machinery.
2. If all tests pass, proceed to the gate as today (record a
   `fixer_feedback` event: ran, 0 failures).
3. If tests fail, the fixer gets ONE additional driven pass within
   the same cycle: its brief lists the failing test names plus, per
   failing test, the FAIL detail excerpt (assertion/traceback tail,
   bounded ~800 chars each), and instructs it to return corrected
   files for the same target set. Then proceed to the gate (no second
   feedback pass this cycle — the gate is the next check).
4. If the acceptance file is missing or itself errors at collection,
   record that in the event and proceed to the gate (never fatal).
5. The acceptance file remains uneditable by FIXER (existing guard
   covers both passes). Feedback-pass model calls charge to the same
   cycle budget/ledger. Ratchet, failure-cycle, and escalation
   semantics unchanged — feedback happens inside the cycle, before
   the gate sees the artifact.

## Tests

- A fixer result that fails the acceptance file triggers exactly one
  feedback pass whose brief contains the failing names + excerpts; a
  second failing result goes to the gate without a third pass.
- A passing fixer result triggers no feedback pass.
- Missing/broken acceptance file: event recorded, cycle proceeds.
- The feedback pass cannot edit the acceptance file (guard intact).
- Suite fully green (210 existing + new).

## Acceptance

Suite green; coordinator runs the live pipeline: success = a
`fixer_feedback` event fires live with real failing names, and the
fix cycle's trajectory improves on verify-29's flat 13 (or closes);
the full gate sequence is reported either way.

## Stage 29 implementation

- Added public QA acceptance execution after the driven FIXER completes its
  items, before normal stage advancement and gate evaluation, through the
  existing command runner with read-only mounts and the existing command/time
  budget. The feedback path never consults private grading configuration.
- Recorded `fixer_feedback` outcomes for passing, failing, missing, collection
  error, and unavailable execution cases. Failures supply test names and
  traceback/assertion tails bounded to 800 characters each.
- Added one persisted, resumable feedback pass over the same target set, using
  the same task ledger, write validation, and checkpoints. No second acceptance
  feedback check or third pass is granted. Acceptance remains uneditable.
  Gate, ratchet, failure-cycle, escalation, and budget settings are unchanged.
- Added seven tests covering failure feedback and its contents, passing/missing/
  broken acceptance, resumed feedback, private-grader exclusion, and rejection
  of an acceptance-edit action envelope during feedback.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 217 tests in 15.559s` / `FAILED (errors=9)`.
  All seven new tests passed; the nine errors are existing HTTP-server tests
  denied socket binding by the sandbox (`PermissionError: [Errno 1] Operation
  not permitted`). No separate loopback/model-server verification was attempted.
- Token usage: not reported by the runtime for this implementation session.
- Nothing staged or committed.
