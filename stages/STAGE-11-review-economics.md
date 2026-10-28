# Stage 11 — REVIEW call economics: caps, prompt diet, deadline retry

Branch: `codex/stage-11-review-economics` from the tip of `codex/stage-10-review-drive`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence (verify-12, first live run of the stage-10 driven REVIEW)

The re-spec WORKED mechanically: no action loop, no section exhaustion, no
re-litigation. INTAKE passed; the intake REVIEW's driven calls flowed — six
model calls finished (1.3s–158s each). The stage then died on its seventh call:
`model_request_killed_on_deadline`, NOT classified retryable, so stage 9's
attempts never engaged and the run escalated.

Diagnosis, in order of leverage:
1. The verdict call uses the stage's full completion cap (8192) although its
   contract is a single first line (`APPROVE` / `REQUEST_CHANGES`). A rambling
   generation burns minutes before the parse can even happen.
2. Section calls also use the full cap; one measured section generation ran
   158 s. Review sections are assessments, not essays.
3. Prompts are bloated: one section call measured 9,162 prompt tokens for a
   19-line spec — previously accepted sections and harness context are being
   re-sent per call, and prefill is this lane's scarcest resource.
4. A deadline kill on a slow-but-healthy lane is terminal. It should be a
   retryable model error like the other model-side failures stage 9 handles.

## Changes

1. **Verdict call cap = 128 completion tokens**, prompt instructs a one-line
   answer. (The strict first-line parse already exists; this just stops paying
   for prose after it.)
2. **Section call cap = 2,048 completion tokens.**
3. **Prompt diet:** each section call carries its section charter + the
   artifact(s) under review + only the source spans that section concerns. It
   must NOT carry previously accepted sections. The verdict call MAY carry the
   accepted sections' text (it judges the whole), still under the 128-token
   completion cap.
4. **Classify `model_request_killed_on_deadline` as a retryable model error**
   for stage attempts (same allowlist family as the format-exhaustion
   classes), still bounded by the shared stage budget envelope — no budget
   multiplication.

Out of scope: timeout VALUES themselves; other stages' caps or prompts;
validators; artifact format; stage-9 policy beyond the classification in (4).

## Tests

- Scripted: verdict call is issued with cap 128; section calls with cap 2048.
- Scripted: a section prompt for section N does not contain section 1's
  accepted text; the verdict prompt does contain the accepted sections.
- Scripted: a deadline-killed first attempt is retried by the stage-attempt
  machinery and a clean second attempt succeeds.
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = REVIEW surviving
real model behavior inside a complete run.

## Stage 11 implementation

- REVIEW section calls now request at most 2,048 completion tokens and verdict
  calls at most 128, further bounded by the existing ledger reservation. The
  verdict prompt explicitly requests exactly one line. Timeout values and other
  stages' caps and prompts are unchanged.
- Section prompts omit accepted sections and retain the current section charter,
  prior-stage artifacts, and relevant supporting sources. Explicit source/line
  citations in artifacts narrow supporting inputs to their referenced spans;
  without citations the supporting input remains relevant to the broad
  counterexample, observation, and unresolved assessments. Stage-name assessment
  needs only the artifacts. Verdict prompts include accepted sections.
- The stage-attempt classifier accepts exactly
  `model_request_killed_on_deadline`. The queue keeps that outcome retryable after
  its child has been terminated and joined, rather than marking the lane
  unhealthy and blocking the next attempt. Existing shared stage-budget and
  attempt limits remain in force; no timeout values changed.
- Added scripted coverage for caps, section/verdict context, cited source spans,
  queue availability after a deadline kill, and a deadline-killed first stage
  attempt followed by a successful second attempt.

Verification: `python3 -m unittest discover -s tests`

Suite result lines:

```text
Ran 120 tests in 10.791s
FAILED (errors=9)
```

111 tests passed, including all Stage 11 tests. The nine errors are existing
socket-based tests whose local server binds fail with
`PermissionError: [Errno 1] Operation not permitted` in this sandbox. Full green
verification and live pipeline verification remain for the coordinator in an
appropriate environment. No separate loopback/model-server calls were made.
Nothing was staged or committed; pre-existing working-tree changes were preserved.
Token usage was not reported by the execution tools.
