# Stage 18 — Runtime-Driven INTEGRATOR + caps for the remaining loop stages

Branch: `codex/stage-18-integrator-drive` from the tip of `codex/stage-17-call-deadlines`.
Do NOT commit (the coordinator commits). Do NOT run loopback/model calls from the
sandbox; live verification is the coordinator's job. Suite: `python3 -m unittest
discover -s tests`.

## Evidence

INTEGRATOR is now the least reliable stage in the pipeline (verify-13 g1,
verify-14 FAILED g3 format exhaustion, verify-16 g2, verify-18 g1,
verify-19 FAILED g3 — terminal `model_request_killed_on_deadline`: an
uncapped 8,192-token action-loop generation outlived even Stage 17's
size-aware deadline in a slow decode window). It is also the most
automatable stage: its real work is collecting CODER's accepted deliverables
into the package layout, writing integration notes, and running the
integrated check — file mechanics plus one prose artifact.

Every driven conversion so far has transformed its stage (REVIEW 10+ live
passes at g1; CODER ~1 minute at g1 twice; QA g1 with caps). INTEGRATOR is
the last free-action-loop producer.

## Changes

1. **Driven INTEGRATOR** (mirror the coder.py/qa.py patterns):
   - The runtime deterministically assembles the package: it collects the
     CODER stage's accepted deliverables into exactly the deliverable
     layout/files the current INTEGRATOR stage produces (preserve the
     existing deliverable set, paths, and stage-gate expectations byte-for-
     byte in structure — only the actor changes).
   - ONE driven call (cap 2,048) produces `integration_notes.md` content as
     plain text, with the package manifest + CODER handoff as context. One
     re-ask on empty/invalid; then the model-error class.
   - The runtime runs the integrated check via the existing executor (the
     same check the loop stage ran — find it in the current flow; if it was
     model-chosen, use CODER's recorded self-check command, falling back to
     the package's test entry point). Results recorded in the existing
     event/artifact shapes.
   - Resume across attempts keyed by input hashes (mirror Stages 15-17):
     assembly + notes persist; a retry redoes only what's missing. Changed
     inputs discard progress.
   - Generated handoff as today.
2. **Completion caps for the remaining action-loop stages** (INTAKE and
   FIXER): their model calls are capped at 4,096 completion tokens (their
   actions are small structured writes and targeted edits; this halves the
   worst-case runaway-generation window that killed verify-19's INTEGRATOR).

Out of scope: driven stages' flows (REVIEW/CODER/QA), the executor,
budgets beyond item 2's caps, stage-9 policy.

## Tests

- Scripted: driven INTEGRATOR produces the same deliverable set/paths as
  the loop version's fixtures expect; notes file content is byte-exact
  from the scripted reply.
- The integrated check runs and its result is recorded; a failing check
  does NOT fail the stage (it is evidence for REVIEW/QA), matching current
  gate semantics — verify against the current implementation and preserve
  whichever semantics it has; note what you found in the summary.
- Resume: an attempt killed after assembly resumes with only the notes
  call outstanding; changed input hash restarts fully.
- INTAKE and FIXER calls carry cap 4096 (assert via the scripted client).
- Suite fully green.

## Acceptance

Suite green; coordinator runs the live pipeline. Success = a complete
9-stage run (AWAITING_COORDINATOR).

## Stage 18 implementation

- Added runtime-driven INTEGRATOR: collect the current CODER deliverables in
  their existing declared layout, preserve any exact declared wiring
  deliverables, and generate only `integration_notes.md` with one plain-text
  call capped at 2,048 completion tokens. Preserve accepted notes bytes exactly;
  re-ask once for invalid/empty notes, then use the existing
  `integrator_action_format_exhausted:` model-error class.
- The fixtures use an in-place package, not a separate copied package tree.
  INTEGRATOR's deliverables remain `artifacts/integration_notes.md` and
  `reports/integration.json`; CODER implementation/test paths remain unchanged.
  Current bytes are retained after FIXER, rather than restoring stale CODER
  contents. The notes prompt contains the file/hash manifest and accepted
  CODER handoff. The runtime invokes the existing named full-suite commands
  (`suite` in both example plans) through the unchanged executor, preserving
  declared captures, attempt-owned receipts, checkpoints, and tool events.
- Added lease-fenced, input-hash-keyed progress for assembly, notes, and check
  receipts/output files. Reclaimed attempts restore accepted work and execute
  only outstanding checks; changed source bytes, CODER handoff, or configuration
  invalidate saved progress. Handoff generation and stage checks remain shared
  with the existing runtime.
- Gate semantics found: `_check_stage` requires nonempty checkpointed
  deliverables, unchanged hashes, valid embedded citations, and a ready
  handoff. It does **not** reject INTEGRATOR on a nonzero suite exit. Failed
  checks remain recorded evidence and the stage succeeds; REVIEW/QA proceed.
  The later satisfiability gate and coordinator recheck independently evaluate
  INTEGRATOR/QA command receipts. Those semantics are preserved.
- Capped remaining INTAKE/FIXER action-loop calls at 4,096 completion tokens.
  REVIEW/CODER/QA flows, executor behavior, and Stage 9 policy are unchanged.
- Updated scripted pipeline fixtures and added coverage for exact notes bytes,
  deliverable/wiring paths, nonzero check evidence, invalid-note exhaustion,
  both remaining loop caps, and resume/invalidation across attempts.

Verification: `python3 -m unittest discover -s tests`

```text
Ran 156 tests in 13.198s
FAILED (errors=9)
```

147 tests passed, including all Stage 18 tests and both scripted full-pipeline
scenarios. The nine errors are existing HTTP tests blocked while binding
localhost servers (`PermissionError: [Errno 1] Operation not permitted`): one
in `test_adversarial`, one in `test_harness`, and seven in `test_model`.
No assertion failures remain. No manual loopback/model-server calls were made;
the mandated suite's HTTP fixtures failed at bind before making their calls.
The suite is not fully green in this sandbox; coordinator verification remains
necessary. `git diff --check` passed. Nothing was staged or committed.

Token usage: not reported by the session tooling.
