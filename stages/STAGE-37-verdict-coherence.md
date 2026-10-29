# Stage 37 — Verdict contract coherence: cap + prompt + parser (one contract)

Branch: `codex/stage-37-verdict-coherence` from main's tip (d4c7292).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Full causal chain (verify-36 AND verify-37, from primary records)

Three facts, all measured, together explain both aborts:
1. Stage 35 made the REVIEW verdict carry discrepancy_resolutions
   as a JSON object {"verdict": ..., "discrepancy_resolutions": [...]}.
   The model complies.
2. Stage 36 taught parse_verdict (review.py) to accept that JSON —
   but verify-37 STILL aborted with unparseable_verdict. The board's
   persisted raw verdict response is 354 chars and json.loads fails
   with "Unterminated string": the response is SEVERED mid-string.
3. The severing mechanism: review.py's driven-section cap is
   `128 if self.current == 'verdict' else 2048` (Stage 11 economics).
   A verdict + several resolution entries (identity hash + disposition
   + note each) cannot fit in 128 completion tokens; every non-trivial
   JSON verdict is truncated into unparseability. The only verdicts
   that ever passed were empty-resolution ones that fit the cap.
   Additionally the verdict section's contract text still says
   "first line must be exactly APPROVE or REQUEST_CHANGES... No JSON
   or envelope" — instructing against the Stage 35 contract.

## The change (one coherent contract)

1. **Cap.** The driven REVIEW verdict call's completion cap rises
   from 128 to 1024 tokens (still below the 2048 section cap).
2. **Contract text.** The verdict section's contract instructs the
   Stage 35 JSON form: reply with exactly one JSON object
   {"verdict": "APPROVE"|"REQUEST_CHANGES",
    "discrepancy_resolutions": [...]} and nothing else. Remove the
   "No JSON or envelope" / first-line instructions from this
   section's contract.
3. **Parser.** parse_verdict stays as Stage 36 left it (JSON first,
   legacy first-line fallback). Do NOT make it tolerate truncated
   JSON — truncation is a cap failure, not a format variant.
4. Nothing else changes.

## Tests

- A scripted driven REVIEW whose verdict response is a JSON object
  carrying several discrepancy_resolutions (a payload of roughly
  400-700 tokens of content) completes the stage and lands the
  resolutions in the ledger.
- The verdict cap for the verdict section is the new value (pin it
  via the existing cap accessor used by the runtime).
- parse_verdict on a truncated JSON sample still raises
  PolicyError (pin the no-tolerance rule).
- Legacy plain-text verdicts still parse.
- Suite fully green (254 existing + new).

## Acceptance

Suite green; coordinator live run: REVIEW stages pass with
resolution-carrying JSON verdicts, gate evaluations occur, and the
run's trajectory is reported against verify-35's best 9.

## Stage 37 implementation

- Raised the driven REVIEW verdict completion cap from 128 to 1024; other
  sections remain at 2048.
- Made the verdict section require exactly one JSON object with `verdict`
  and `discrepancy_resolutions`, removing the conflicting first-line/plain-text
  instructions and the alternative-format wording for discrepancy closure.
- Left `parse_verdict` unchanged. Added regression coverage for truncated JSON
  rejection, the cap accessor, and a scripted five-resolution JSON verdict
  (roughly 400–700 tokens) that completes REVIEW and persists all resolutions
  in the ledger. Existing legacy verdict coverage remains in place; updated
  runtime cap and prompt expectations, including resumed reviews.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 257 tests in 18.350s` / `FAILED (errors=9)`.
  All 248 other tests passed, including the three new tests. The nine errors
  are existing HTTP tests whose local server socket binds fail with
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  No separate loopback or model-server calls were made.
- Token usage: not reported by the runtime.
- Nothing staged or committed.
