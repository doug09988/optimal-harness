# Stage 50 — Regression ratchet: deliverable-growth exemption

Branch: `codex/stage-50-ratchet-growth` from main's tip (5a225f5).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-51, measured)

Trajectory 6→12→12, ESCALATED by the ratchet: "three failed
cycles on same defect: REGRESSION: best failure count: 6;
regressed failure count: 12. Best artifact restored." Cycle 1's
6 included test_unit.py MISSING (an import-error blob worth ~2
entries for the whole suite). The cycle-2 FIXER landed
test_unit.py — real progress — and the gate rose to 12 because
the suite's individual (mostly wrong) tests now run and count.
Cycles 2–3 at 12 counted as the same regression defect three
times; the run escalated and restored the cycle-1 state. The
artifact never regressed: hidden grader green every cycle and
every accepted artifact write probe-verified. The ratchet
compares raw counts, so growing the present-deliverable set —
structural progress that enlarges the counted denominator —
reads as regression. The adjudication conveyor (which exists
precisely to burn down a landed suite's wrong tests) never got
its runway: only 1 quarantine fired before the escalation.

## The change

In the cycle/regression bookkeeping (find the failed_cycle /
same-defect / best-artifact-restore logic):
- Track, per cycle, the set of PLAN-DECLARED deliverables
  present (written and non-empty) at gate time.
- If the current cycle's present set contains a declared
  deliverable that the best cycle's set lacked, this cycle
  does NOT increment the regression same-defect counter, and
  it becomes the new comparison baseline for subsequent
  cycles (its count and present set). Rationale: the count is
  not comparable across different present sets.
- Artifact restoration ("best artifact restored") must never
  delete or revert a deliverable that was absent at the best
  cycle: restoration applies per-file to files present at
  best; files landed after best persist.
- Only plan-declared deliverables count for growth (a stray
  new file does not reset the clock). The run-level cycle cap
  still bounds total cycles. No change to what the gate
  counts, to adjudication, or to the same-defect rule for
  cycles whose present set is unchanged.

## Tests

- Scripted cycles: best=6 with deliverable X missing → cycle
  lands X, count 12 → no regression increment, baseline moves;
  two further flat cycles at 12 with unchanged present set DO
  increment (clock still works after growth settles).
- Restoration after a growth cycle preserves the newly landed
  deliverable's content while reverting a file that regressed
  vs best.
- A non-declared new file does not trigger the exemption.
- Ratchet behavior for unchanged present sets is byte-identical
  to today (existing tests keep passing). Suite fully green
  (305 existing + new).

## Acceptance

Suite green; coordinator live run: landing a missing declared
deliverable no longer escalates a run by itself; the
adjudication conveyor gets the cycles it needs. Zero-failure
gate pass (AWAITING_COORDINATOR) remains the goal.

## Stage 50 implementation

- Gate-time bookkeeping records the sorted set of plan-declared, non-empty
  deliverable files in each evaluation and its saved baseline. New declared
  presence advances the comparison baseline, including its failure count and
  checkpoint snapshot, without incrementing the defect counter. The cycle
  number still advances; unchanged-set defect counting is preserved.
- Restoration filters declared files against the saved baseline's present set,
  preserving deliverables landed later, including when the old snapshot held
  an empty checkpoint for them. Restored files still publish checkpoints.
- Added four scripted tests covering 6→12→12 growth and subsequent repeat
  escalation, per-file restoration, undeclared files, and empty deliverables.
  Gate counting and adjudication are unchanged.
- Verification: `python3 -m unittest discover -s tests`.
  Suite result: `Ran 309 tests in 35.016s` / `FAILED (errors=9)`.
  The other 300 tests passed, including all Stage 50 tests. All nine errors
  are existing HTTP tests blocked at local server bind with
  `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  No separate loopback or model-server calls were made.
- `git diff --check` passed. Nothing staged or committed.
- Token usage: not reported by the runtime.
