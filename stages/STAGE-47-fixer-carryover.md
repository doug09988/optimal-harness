# Stage 47 — FIXER handoff carry-over + degrade coverage for handoff exhaustion

Branch: `codex/stage-47-fixer-carryover` from main's tip (82099d5).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-47, measured)

Gate 1 = 5 with a probe-verified artifact — Stage 46 worked in
cycle 1 (the CODER's +1 write was rejected at the write path with
the probe diff, then corrected). The run then died in cycle 2:
the FIXER attempted to rewrite wordfreq.py, its writes were
probe-rejected, the file was never re-checkpointed in that task,
and the handoff readiness check failed all remaining generations
with `fixer_handoff_exhausted:error:missing_deliverables:
missing_or_uncheckpointed_deliverables:wordfreq.py` → ESCALATED.
Two seams:
A. No carry-over: a deliverable the FIXER attempted but failed
   to rewrite acceptably loses its prior checkpointed content
   for handoff purposes, even though a good version exists from
   earlier in the run. The package should remain whole.
B. Degrade coverage gap: Stage 45's degrade wrapper triggers on
   the `fixer_action_format_exhausted:` prefix only. This
   failure arrived as `fixer_handoff_exhausted:` — same family
   (the FIXER could not produce an acceptable result), different
   string, no degrade. The run died on a naming technicality.

## The change

A. **Carry-over.** When composing a FIXER (or any stage)
   handoff, a declared deliverable that has a checkpointed
   version from earlier in the run and was not successfully
   rewritten in this task is carried over at its prior content;
   record a `deliverable_carried_over` event naming the path.
   missing_or_uncheckpointed may then arise only for a
   deliverable that has never been checkpointed in the run.
   (Stage 34/43 marks are unaffected: a deliverable_failed file
   that never existed still defers as before.)
B. **Degrade coverage.** The FIXER degrade path triggers for
   the whole FIXER exhaustion family — action-format exhaustion
   AND handoff exhaustion arising from the fixer's own
   unaccepted work — not budget stops. Implement by inverting
   the condition: degrade on RuntimeError from the fixer UNLESS
   the error is a hard stop (budget/circuit-breaker markers,
   which keep their current propagation). The degraded
   completion records the triggering error verbatim.

## Tests

- FIXER whose artifact rewrite is probe-rejected every time:
  the prior artifact is carried over (event recorded), the
  handoff completes, the stage degrades or completes with the
  unfixed items recorded, and the cycle reaches a gate — no
  escalation.
- fixer_handoff_exhausted from the fixer's own work degrades;
  a budget-stop error from the fixer still propagates and
  stops the run.
- A never-checkpointed deliverable still raises
  missing_or_uncheckpointed (Stage 43 deferral path intact).
- Suite fully green (293 existing + new).

## Acceptance

Suite green; coordinator live run: a FIXER that cannot improve
the artifact leaves the run with the last good artifact and a
gate evaluation, not an escalation; trajectory reported against
verify-47's cycle-2 death. Zero-failure gate pass
(AWAITING_COORDINATOR) remains the goal.

## Stage 47 implementation

- Handoff composition and readiness now carry forward the latest archived,
  checkpointed bytes for declared deliverables without an accepted checkpoint
  in the current attempt. Each restoration records `deliverable_carried_over`
  with the path, digest, and source task. Archive integrity is checked before
  restoration; never-checkpointed deliverables still fail readiness, and the
  Stage 34/43 failed-deliverable deferral remains intact.
- Final-generation FIXER RuntimeError handling now degrades unless the error
  is a budget/circuit-breaker hard stop. Both errors from FIXER execution and
  finish-validation handoff exhaustion use that handling. The triggering error
  is preserved verbatim in the degraded summary. Existing generation retries,
  probe gating, gate accounting, and Stage 34/43/45 behavior are preserved.
- Added five offline regressions covering probe-rejected carry-over reaching a
  gate with unfixed items, archive restoration, never-checkpointed readiness,
  verbatim handoff exhaustion degradation, real finish-validation exhaustion,
  and budget-stop propagation.
- Verification: `python3 -m unittest discover -s tests`
  reported `Ran 298 tests in 34.138s` / `FAILED (errors=9)`.
  All five new tests passed; the nine errors are existing HTTP-server tests
  failing to bind local sockets with `PermissionError: [Errno 1] Operation not
  permitted` in this sandbox. The remaining 289 tests passed. No separate
  loopback/model-server calls were made. Coordinator verification remains
  required for a fully green suite and the live gate trajectory.
- Token usage: not reported by the runtime.
- Nothing staged or committed.
