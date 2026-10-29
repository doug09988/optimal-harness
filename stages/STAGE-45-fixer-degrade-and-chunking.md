# Stage 45 — FIXER exhaustion degrades to the gate + chunked repair writes

Branch: `codex/stage-45-fixer-degrade-and-chunking` from main's tip (ffa6eb8).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-44, measured)

The Stage 43/44 chain worked: gate 1 = 4 with the deferred file
counted, fixer_allowlist_extended fired. Then the cycle-1 FIXER
burned all 3 generations on the test_unit.py repair — every write
syntax-rejected (unterminated strings / paren mismatches at
various lines) — and its exhaustion ESCALATED the whole run
(`fixer_action_format_exhausted:unfixed`). Two gaps:
A. FIXER exhaustion is still run-killing. CODER deliverable
   exhaustion degrades (Stage 34); the FIXER's does not. The
   gate + defect clock are the run's legitimate terminators —
   every stage's exhaustion should degrade to the gate.
B. The root mechanical failure: whole-file single-shot writes
   of a ~350-line test file keep producing mid-file syntax
   errors, across both CODER and FIXER, run after run. The
   driven protocol already chunks long CONTENT for sections;
   file writes have no such discipline.

## The change

A. **FIXER degrade.** When the FIXER stage exhausts its
   generations with items still unfixed, the stage completes
   the cycle in a degraded state instead of failing the run:
   each unfinished item is recorded (`fixer_item_unfixed` event
   with its final error), the deliverables it targeted keep
   their deliverable_failed state, the FIXER handoff reports the
   unfixed items as open discrepancies for the closure ledger,
   and the cycle proceeds to its gate evaluation. Budget
   exhaustion (hard caps) still stops the run as today — this
   covers format/content exhaustion only.
B. **Chunked repair writes.** In the driven FIXER, a write
   whose target file exceeds ~150 lines is performed as
   sequential segment writes: the model produces the file in
   segments of at most ~60 lines per call (segment markers are
   runtime-owned; the model supplies plain content per segment,
   ending with a completion marker the runtime defines), the
   runtime concatenates the segments and runs the existing
   validation (fence repair, byte-compile) on the ASSEMBLED
   file. A segment-level format failure retries only that
   segment. Files at or under the threshold keep the current
   single-shot path. The assembled-file rejection reasons keep
   Stage 31/32 behavior (±3-line context).
   Scope B to the FIXER write path only (CODER keeps its
   current path + Stage 34 isolation).

## Tests

- FIXER exhausting on a repair item: stage completes degraded,
  fixer_item_unfixed recorded, the cycle reaches a gate, no
  run abort. Budget-cap exhaustion still aborts.
- Chunked write: a >150-line file is assembled from segments
  and compiles; a broken segment retries in isolation; the
  assembled file with a syntax error is rejected with the
  usual context; a ≤150-line write uses the single-shot path.
- The verify-44 scripted shape (deferred test_unit.py, three
  bad generations) now ends at a gate evaluation, not an
  escalation.
- Suite fully green (281 existing + new).

## Acceptance

Suite green; coordinator live run: a FIXER that cannot land a
repair degrades to the gate instead of killing the run, and a
large repair file is written in segments; trajectory reported
against verify-44's cycle-1 FIXER death. Zero-failure gate
pass (AWAITING_COORDINATOR) remains the goal.

## Stage 45 implementation

- The driven FIXER retains the existing three-generation retry allowance. On
  final format/content exhaustion it now completes with a degraded, ready
  handoff, records `fixer_item_unfixed` with each unfinished target's final
  error, retains `deliverable_failed` evidence, and exposes the failures as
  open discrepancies. Failed FIXER targets are excluded from stage readiness
  requirements so the cycle can continue to its gate. Hard budget-cap stops
  still abort; CODER paths and gate accounting are unchanged.
- FIXER targets over 150 existing lines now use sequential segments of at most
  60 content lines. The runtime defines segment numbering and the terminal
  `<<<FIXER_MORE>>>` / `<<<FIXER_DONE>>>` markers. Malformed segments retry in
  isolation; only the concatenated file enters the existing write validation,
  fence repair, syntax checking, and error-context path. Targets at or below
  150 lines retain the single-shot prompt. Acceptance feedback remains in
  segment prompts.
- Added six scripted regression tests covering final repair exhaustion reaching
  a gate (including the cycle-local deferred-target allowlist extension), plan
  exhaustion, hard budget termination, segmented assembly with isolated retry,
  assembled syntax rejection with context, and the 150-line single-shot boundary.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 287 tests in 27.516s` / `FAILED (errors=9)`.
  All six new tests pass; no assertion failures. The nine existing HTTP tests
  error at local server binding with `PermissionError: [Errno 1] Operation not
  permitted` in this sandbox. The initial 281-test suite had the same nine
  environment errors. No separate loopback/model-server calls were made.
- Token usage: not reported by the runtime for this implementation session.
- No files staged or committed. Coordinator live-run validation remains pending.
