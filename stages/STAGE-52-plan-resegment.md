# Stage 52 — Plan parsing: re-segment run-on plan replies

Branch: `codex/stage-52-plan-resegment` from main's tip (ef4c394).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-48 + verify-53, measured)

Both runs died at the CODER's first step:
`coder_action_format_exhausted:plan; plan requires path ::
purpose, one per line`, 3/3 generations each. The board's raw
payloads (verify-53) show the actual miss shape: the replies
BEGIN correctly — `wordfreq.py :: implementation of the word
frequency counter CLI tool` — but the entries run on WITHOUT
newlines: `...CLI tool test_unit.py :: purpose test_acceptance.py
:: purpose...`, the whole plan on effectively one line. The
per-line parser rejects it and the model regenerates the same
run-on shape every generation. Same tolerant-parsing family as
Stages 41/48/49: the content is right, the framing is off by a
mechanical slip the model cannot see.

## The change

At the plan validation/parsing site (find where "plan requires
path :: purpose" is enforced for the driven CODER):
- First try today's strict per-line parse; if it succeeds,
  behavior is unchanged.
- If it fails, attempt re-segmentation: find every occurrence
  of a path-like token (a non-whitespace token containing a
  `.` and ending in a file suffix such as .py/.md/.json/.txt)
  immediately followed by ` :: `, and split the text at those
  boundaries so each segment begins with its path. Re-run the
  strict per-line validation on the re-segmented lines.
- If the re-segmented plan validates, accept it and record a
  `plan_repaired` event (mirroring write_repaired's disclosure
  style) naming the repair kind run_on_segmentation.
- If it still fails, reject exactly as today with the original
  error. No other format is newly accepted: a reply with no
  ` :: ` boundaries still fails; purposes keep their text
  verbatim (only the missing newlines are inserted).

## Tests

- The verbatim verify-53 shape (three entries run onto one
  line) parses to the same plan as its newline-separated
  equivalent; event recorded.
- A normal multi-line plan parses exactly as today (no event).
- A run-on reply whose segments still fail validation (e.g. a
  segment with no purpose) rejects with the original error.
- Text with ` :: ` inside a purpose but no path-token boundary
  is not split spuriously (the boundary requires a path-like
  token before the separator).
- Suite fully green (313 existing + new).

## Acceptance

Suite green; coordinator live run: run-on CODER plans are
accepted with disclosure instead of exhausting the stage.
Zero-failure gate pass (AWAITING_COORDINATOR) remains the goal.

## Stage 52 implementation

- Kept the existing strict per-line parsing, sanitization, file cap, and required-file
  validation first. Only after failure, insert newlines before path-like tokens
  ending in file suffixes followed by ` :: ` and repeat the same validation.
- Successful fallback records `plan_repaired` with
  `repair_kind=run_on_segmentation`, task, and generation at the CODER plan site.
  Failed fallback raises the original error. Purpose text is unchanged apart from
  the existing strict parser's whitespace trimming.
- Added four tests covering three run-on entries matching their multiline plan,
  event disclosure through the runtime, unchanged strict acceptance without an
  event, original errors on failed repair/no boundaries, and purpose separators
  without a path-like token.
- Verification: `python3 -m unittest discover -s tests`:
  `Ran 317 tests in 35.288s` / `FAILED (errors=9)`.
  All four new tests passed; the nine existing errors are sandbox-denied HTTP
  server binds (`PermissionError: [Errno 1] Operation not permitted`) in
  test_adversarial, test_harness, and test_model. Full green verification remains
  pending an environment permitting those existing tests. No live model calls
  were made.
- Token usage: not reported by the session tooling.
- Nothing staged or committed. Coordinator live acceptance remains
  AWAITING_COORDINATOR.
