# Stage 15 — QA call economics + resumable driven sequence

Branch: `codex/stage-15-qa-resume` from the tip of `codex/stage-14-accept-split`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-16, 2026-10-28)

Seven of nine stages passed (INTEGRATOR g2, everything else g1; driven REVIEW
is now 9-for-9 at generation 1 across four runs; accept-and-split absorbed two
oversized writes live). The run died at QA — driven QA's first live exercise —
with terminal failure `model_request_killed_on_deadline`:

- QA attempt 1 got deep (acceptance tests were assembled and executed — the
  attempt record references the acceptance.json capture) before a call died.
- Attempts 2 and 3 died FAST, early in the sequence: each stage attempt
  restarts the whole driven sequence from zero, so a slow lane re-bills the
  full plan + per-test prefill/generation marathon until the same deadline
  wall kills it again.
- Root economics: QA's driven calls still use the full 8192 completion cap
  (Stage 11 capped REVIEW's calls, not QA's). On this lane (~15 tok/s decode
  in bad windows), one uncapped call can exceed the per-call deadline alone.

## Changes

1. **Cap QA's driven calls** (same discipline as Stage 11):
   - test-plan call: 512 completion tokens (it returns names, one per line);
   - per-test calls: 2,048 (one test function's code);
   - report call: 1,024 (first-line verdict + findings prose).
   Timeouts themselves: unchanged.
2. **Make the driven QA sequence resumable across stage attempts.** Persist
   the sequence's progress as runtime state keyed by (project, stage,
   hash of the artifact-under-test inputs): the accepted plan and each
   accepted test body are stored as they complete (the artifact checkpoint
   machinery already exists — use it or mirror its pattern). A retried
   attempt RESUMES: it re-uses the stored plan and every accepted test,
   and only calls for tests not yet accepted; execution and the report call
   run once the full set is present. If the input artifact hash differs
   from the stored key, the stored progress is discarded and the sequence
   starts fresh (a fixer cycle must never inherit stale tests).
3. Deadline kills remain stage-9-retryable (Stage 11) — with resume, a
   retry now continues instead of restarting.

Out of scope: other stages; the executor; budgets; timeout values; the
`unwritable` semantics from Stage 12 (an unwritable test stays unwritable
across resumes of the same input hash).

## Tests

- Scripted: QA calls are issued with caps 512 / 2048 / 1024 respectively.
- Scripted: an attempt killed after 2 of 4 tests are accepted resumes on
  the next attempt — the client receives calls ONLY for the remaining 2
  tests (plus execution + report), and QA completes.
- Scripted: changing the artifact-under-test hash discards stored progress
  (full sequence re-runs).
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline.

## Stage 15 implementation

- Capped driven QA plan, per-test, and report calls at 512 / 2,048 / 1,024
  completion tokens respectively, within the existing reserved cap. Budgets,
  timeout values, deadline retry classification, and the sandbox executor are
  unchanged.
- Added lease-fenced SQLite runtime progress per project and QA stage, validated
  against a hash of the specification, permitted inputs, implementation artifacts,
  immutable inputs, and command inputs (excluding QA outputs). A changed hash
  discards saved progress, including after a fixer changes command-only code.
- Persisted the accepted plan, accepted test bodies, unwritable tests, validation
  counts, and correction feedback. Retries request only unfinished tests, assemble
  in plan order, then execute and request the report. A retry after all tests were
  accepted executes the assembled tests before requesting its report.
- Added four scripted tests for call caps and two-of-four resume, artifact hash
  invalidation, unwritable preservation, and complete-test resume before reporting.
- Verification: `python3 -m unittest discover -s tests`
  Suite result lines: `Ran 136 tests in 12.221s` / `FAILED (errors=9)`.
  All four new Stage 15 tests passed; the nine errors are existing localhost HTTP
  tests blocked by sandbox socket binding (`PermissionError: [Errno 1] Operation
  not permitted`). No separate loopback or model-server calls were attempted.
- Token usage: not reported by the session runtime.
