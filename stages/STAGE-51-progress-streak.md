# Stage 51 — Same-defect clock: strict progress breaks the streak

Branch: `codex/stage-51-progress-streak` from main's tip (2b81ae1).
Do NOT commit (the coordinator commits). Do NOT run loopback/model
calls from the sandbox. Suite: `python3 -m unittest discover -s tests`.

## Evidence (verify-52, measured)

Trajectory 6→5→8→7, ESCALATED at cycle 4: the same-defect
tracker (failed_cycle events) counted defect f0fd4c7060f64cbf
at cycles 2, 3, 4 (repeats 1→2→3) and fired — while the gate
fingerprints rotated every cycle, cycle 3 was deliverable
growth (Stage 50 exempted it from the REGRESSION ratchet, but
this separate same-defect counter kept counting), and cycle 4
was strict progress (8→7, composition churning, adjudication
conveyor mid-flight: the landed suite's wrong tests had just
become eligible and their quarantines would first land at
gate 5). The clock's purpose is to stop hopeless stagnation
(verify-40: flat at 1 for three cycles — a correct fire). It
currently also fires during measurable progress, because its
defect identity is a stable core that persists while the count
descends around it.

## The change

In the same-defect / failed_cycle bookkeeping:
- A cycle whose failure count is STRICTLY LOWER than the
  previous cycle's count is progress: it resets the current
  defect streak (repeats back to 0 for the active defect) and
  never increments any defect counter.
- Stage 50's growth exemption is unchanged (a growth cycle
  already does not increment; it also does not reset — growth
  is neutral).
- Flat or rising cycles with an unchanged present-deliverable
  set behave exactly as today: same defect → repeats += 1;
  escalation at 3 stands.
- The run-level cycle cap still bounds total cycles; nothing
  about gate counting, defect identity computation, or
  restoration changes.

## Tests

- Counts 8→7→7→7 on one defect identity: no escalation at the
  progress cycle; the streak rebuilds over the three flat
  cycles and escalates on the third flat one (the clock still
  works, one cycle later than it would today).
- Counts 5→8 (growth) →7→7→7: growth neutral, progress
  resets, escalation only after the flat streak reaches 3.
- Flat-from-the-start (verify-40 shape: 1→1→1) escalates
  exactly as today. Existing ratchet/streak tests keep
  passing. Suite fully green (309 existing + new).

## Acceptance

Suite green; coordinator live run: strictly descending cycles
never escalate; stagnation still does. Zero-failure gate pass
(AWAITING_COORDINATOR) remains the goal.

## Stage 51 implementation

- Read the previous evaluated failure count from durable evaluation events,
  including growth and regression evaluations, rather than the best count.
- Strict progress resets the active defect's persisted streak to zero and
  increments no defect counter. Growth retains Stage 50's neutral behavior.
  Defect identity, gate counting, restoration, and escalation at 3 are unchanged.
- Added deterministic tests for progress followed by three flat cycles,
  growth followed by progress and stagnation, flat-from-start escalation,
  and descending counts above the best count. Changed the existing repeated
  regression test to use flat regression counts so it still tests stagnation.
- Verification: `python3 -m unittest discover -s tests`
  Result: `Ran 313 tests in 35.452s` / `FAILED (errors=9)`.
  All 304 remaining tests passed, including the new tests. The nine errors
  are existing HTTP tests denied binding to `127.0.0.1` by the sandbox
  (`PermissionError: [Errno 1] Operation not permitted`); no server requests
  from those tests were reached. No separate loopback/model-server calls
  were attempted.
- Token usage: not reported by the runtime.
- Nothing staged or committed.
