# Stage 49 — Write-path repair for unterminated string literals

Branch: `codex/stage-49-string-repair` from main's tip (3f31ccf).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-50, measured)

Trajectory 4→3→3→3→3 (coordinator-stopped): the artifact is
fully correct (hidden green every cycle) and 2 of the 3 residual
failures are the absence of test_unit.py. Across the CODER
(3 generations) and the FIXER (cycles 1–2), EVERY test_unit.py
attempt failed byte-compile at the same place — line 8/9,
"EOL while scanning string literal", inside the first test
method: the model writes a string containing a literal newline
(a JSON text or word list broken across lines inside a
single-quoted string) and regenerates the same shape on every
retry, because from the model's side the content looks correct.
Stage 32's fence unwrap proved the pattern: a mechanical,
semantics-preserving repair at the write path beats rejection
when the defect is a form slip the model cannot see.

## The change

Extend the write path's repair-first sequence (the Stage 32
repair site in optimal_harness/runtime.py — wherever fence
unwrap happens today):
- When byte-compile of a Python write fails with
  "EOL while scanning string literal" (unterminated string) at
  line L, attempt a bounded join repair: join line L with the
  following line by replacing the literal newline inside the
  string with the two characters `\n` (backslash-n), i.e. the
  string continues on one physical line with an escaped
  newline; recompile; repeat the join with further following
  lines as needed, at most 4 joins, recompiling after each.
- If any joined candidate compiles, accept it as a repaired
  write under the existing Stage 32 semantics: `write_repaired`
  event naming the repair kind (unterminated_string_join) and
  the line; the accepted content is the joined text.
- If no candidate compiles, reject exactly as today with the
  original error. No other SyntaxError class is repaired.
- Applies to every Python write path user (CODER, FIXER incl.
  chunk-assembled writes, QA, INTEGRATOR) exactly where the
  fence repair applies today — no more, no less.
- Triple-quoted strings are out of scope: if the unterminated
  literal opens with triple quotes, do not attempt the join
  (reject as today).

## Tests

- A file whose test method contains a single-quoted JSON
  string split across two physical lines → repaired write,
  compiles, the string's value contains the newline, event
  recorded.
- A string split across 3 lines → repaired after 2 joins.
- An unterminated string that no ≤4-join candidate fixes →
  rejected with the original error, no event.
- A triple-quoted unterminated literal → not attempted.
- Other SyntaxErrors (paren mismatch etc.) → unchanged
  rejection. Fence repair + Stage 31 validation tests keep
  passing. Suite fully green (302 existing + new).

## Acceptance

Suite green; coordinator live run: test_unit.py-class writes
land via repair instead of exhausting; trajectory reported
against verify-50's permanent-absence failure. Zero-failure
gate pass (AWAITING_COORDINATOR) remains the goal.


## Stage 49 implementation

Extended the shared Stage 32 Python write checkpoint with a bounded
unterminated-string join repair. It escapes the newline at the original
error line, recompiles after each join, and accepts only a compiling
candidate within four joins and the existing file-size limit. Successful
repairs record `write_repaired` with `repair_kind=unterminated_string_join`
and `error_line`; chunked writes store the repaired content. Legacy and
current Python short-string error messages are recognized. Triple-quoted
literals and other SyntaxErrors remain excluded; failed repairs retain
the original rejection error and content context.

Added tests for split JSON strings, three-line and four-join success,
exhausted/unfixable joins, triple-quoted literals, unrelated SyntaxErrors,
original rejection preservation, and driven FIXER storage chunks.

Verification: `python3 -m unittest discover -s tests`.
Suite result: `Ran 305 tests in 35.374s` / `FAILED (errors=9)`.
All 296 other tests, including the new Stage 49 tests, passed.
The nine errors are existing HTTP fixture setup failures: sandbox
socket binds raise `PermissionError: [Errno 1] Operation not permitted`.
No separate loopback or model-server calls were made.
Token usage: not reported by the runtime. Nothing staged or committed.
