# Stage 34 — Deliverable-level failure isolation (one bad file can't kill a run)

Branch: `codex/stage-34-deliverable-isolation` from main's tip
(f457f6a). Do NOT commit (the coordinator commits). Do NOT run
loopback/model calls from the sandbox. Suite:
`python3 -m unittest discover -s tests`.

## Evidence (verify-32 + verify-34, measured 2026-10-28)

Twice now a driven stage exhausted its write-retry budget on ONE
file and the whole run aborted in cycle 1 with zero gate evaluations:
- verify-32: persistent fence wrapping (since repaired by Stage 32).
- verify-34: test_unit.py arrived genuinely syntax-broken in every
  generation (unterminated string at line 306 in one attempt,
  mismatched parenthesis at line 36 in another) while wordfreq.py
  wrote fine — 5 fence repairs succeeded; the run still died with
  `coder_action_format_exhausted:unwritable`.
A single unwritable deliverable must degrade the cycle, not kill the
run: the FIXER exists to repair files and has an execution feedback
loop (Stage 29) suited to exactly this.

## The change (driven stages using the write-validation path)

1. **Isolation.** When a stage's per-file retry budget exhausts on a
   file, that deliverable is marked failed — `deliverable_failed`
   event (stage, path, final validation error) — and the stage
   COMPLETES with its successful deliverables, provided at least one
   deliverable wrote successfully. If EVERY deliverable fails, the
   stage fails as today (same exhausted error).
2. **Handoff to FIXER.** Failed deliverables (path + final error +
   the rejected content's error context: the error line plus 3 lines
   either side, bounded) are seeded into the next FIXER cycle's
   brief as explicit repair items (in cycle 1, they seed the first
   fix cycle). The FIXER may create/repair the file through its
   normal item machinery; the write-validation path applies to its
   writes as today.
3. **Error context in retries.** Within a stage's own retries, the
   retry reason for a validation failure now includes the same
   error-line context (±3 lines), not only the SyntaxError message.
4. Nothing else changes: budgets (the per-file retry budget is the
   same count, it just stops being stage-fatal), adjudication,
   quarantine, gate accounting (a missing/broken file still makes
   its report red that cycle — honestly counted).

## Tests

- A stage with one valid and one persistently-invalid deliverable
  completes, fires deliverable_failed, and the invalid file's error
  + context appear in the next FIXER brief.
- A stage with all deliverables invalid still fails with the
  exhausted error.
- Retry reasons carry the ±3-line context around the error line.
- The gate still counts the missing file's report failure that
  cycle (no accounting change).
- Suite fully green (240 existing + new).

## Acceptance

Suite green; coordinator live run: cycle 1 completes even if a
deliverable is syntax-broken, gate evaluations occur, the FIXER
receives the broken file as a repair item; trajectory and Stage 33
adjudication behavior reported together (both live-untested until
now).

## Stage 34 implementation

- Driven CODER file exhaustion emits `deliverable_failed` with stage, path,
  and final validation error. Successful files permit stage completion;
  all-file failure retains `coder_action_format_exhausted:unwritable`.
  The stage handoff lists successful deliverables and separately records
  failed deliverables; only its local readiness check excludes those failures.
- Python validation errors include numbered rejected-source context around
  the error line (±3 lines, each capped at 400 characters), including after
  fence repair. Retries and the final failure retain that same context.
- The next FIXER brief receives explicit path-bearing repair items from
  trusted successful-stage handoffs since the preceding FIXER, including
  unresolved items from that FIXER itself. FIXER exhaustion also emits
  `deliverable_failed`; its existing item machinery and retry counts remain.
- Tests cover partial success, all-file failure, bounded retry context,
  first-FIXER seeding, subsequent-FIXER seeding, and unchanged counting of
  a missing file's report failure. Budgets, adjudication, quarantine, and
  gate accounting were not changed.
- Verification: `python3 -m unittest discover -s tests`
  Suite result: `Ran 241 tests in 16.184s` — `FAILED (errors=9)`.
  The nine errors are existing HTTP tests blocked at localhost socket bind
  with `PermissionError: [Errno 1] Operation not permitted`; the remaining
  232 tests passed. No separate loopback/model-server calls were made.
  Full green and coordinator live acceptance remain unverified here.
- Token usage: not reported by this session.
- Nothing staged or committed.
